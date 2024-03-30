from datetime import datetime
import json
import os.path
import sys
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import ngrok


class DevNull:
    def write(self, msg):
        pass


sys.stderr = DevNull()


class GoogleDriveMonitor:
    SCOPES = ["https://www.googleapis.com/auth/drive",
              "https://www.googleapis.com/auth/drive.activity.readonly"]
    saved_token_file = "saved_start_page_token.json"

    def __init__(self):
        self.resource_id = None
        self.hook_id = None
        self.saved_start_page_token = None
        self.drive = None
        self.drive_activity = None
        self.creds = None
        self.activity_time: int = int(datetime.utcnow().timestamp() * 1000)

    def connect(self):
        if self.creds is None:
            if os.path.exists("token.json"):
                self.creds = Credentials.from_authorized_user_file("token.json", self.SCOPES)
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    self.creds.refresh(Request())
                else:
                    try:
                        flow = InstalledAppFlow.from_client_secrets_file(
                            "credentials.json", self.SCOPES
                        )
                        self.creds = flow.run_local_server(port=0)
                    except FileNotFoundError:
                        print("Make sure to have \"credentials.json\" in the working directory")

                with open("token.json", "w") as token:
                    token.write(self.creds.to_json())

        if self.drive is None:
            self.drive = build("drive", "v3", credentials=self.creds)

        if self.drive_activity is None:
            self.drive_activity = build("driveactivity", "v2", credentials=self.creds)

    def save_start_page_token(self):
        with open(self.saved_token_file, "w") as f:
            json.dump(self.saved_start_page_token, f)

    def get_start_page_token(self):
        try:
            with open(self.saved_token_file, "r") as f:
                self.saved_start_page_token = json.load(f)
        except FileNotFoundError:
            if self.drive:
                start_page_token = self.drive.changes().getStartPageToken(supportsAllDrives=True).execute()
                self.saved_start_page_token = start_page_token.get('startPageToken')
                self.save_start_page_token()

    def register_hook(self, hook_url: str):

        try:
            self.connect()
            hook_id = str(uuid.uuid4())
            self.get_start_page_token()

            body = {
                "id": hook_id,
                "type": "web_hook",
                "address": hook_url
            }
            response = self.drive.changes().watch(body=body, pageToken=self.saved_start_page_token,
                                                  includeItemsFromAllDrives=True, supportsAllDrives=True).execute()

            if self.hook_id and self.resource_id:
                self.unregister_hook()

            self.resource_id = response.get("resourceId")
            self.hook_id = hook_id

        except HttpError as error:
            print(f"An error occurred: {error}")

    def unregister_hook(self):
        if self.hook_id and self.resource_id:
            self.drive.channels().stop(body={"id": self.hook_id, "resourceId": self.resource_id}).execute()
            self.resource_id = None
            self.hook_id = None

    def review_changes(self):
        page_token = None
        while True:
            results = self.drive_activity.activity().query(
                fields="activities(timestamp,primaryActionDetail,actions)"
                       "activities/targets/driveItem(title,name,driveFile),"
                       "nextPageToken",
                body={"pageToken": page_token,
                      "filter": f"detail.action_detail_case:CREATE AND time > {self.activity_time}"}).execute()
            activities = results.get("activities", [])

            if activities:
                for activity in activities:
                    activity_time = int(
                        datetime.strptime(activity.get('timestamp'), "%Y-%m-%dT%H:%M:%S.%fZ").timestamp() * 1000)
                    if activity_time <= self.activity_time:
                        break

                    targets = activity.get("targets", [])
                    for target in targets:
                        item = target.get('driveItem')
                        file_id = item.get('name').split('/')[1]
                        print(f"{activity.get('timestamp')}: {item.get('title')} (id: {file_id}) has added to drive.")
                        try:
                            permissions = self.drive.permissions().list(fileId=file_id,
                                                                        fields="permissions(id,type)").execute()
                            for perm in permissions['permissions']:
                                if perm['type'] == 'anyone':
                                    print(f"\033[32m\t\t -- Remove new file {item.get('title')} "
                                          f"(id: {file_id}) \"anyone\" permission.\033[0m")
                                    self.drive.permissions().delete(fileId=file_id,
                                                                    permissionId=perm['id']).execute()
                        except HttpError as e:
                            # print(e)
                            pass

                save_activity_time = int(
                    datetime.strptime(activities[0].get('timestamp'), "%Y-%m-%dT%H:%M:%S.%fZ").timestamp() * 1000)

                if save_activity_time > self.activity_time:
                    self.activity_time = save_activity_time

            page_token = results.get("nextPageToken", None)
            if page_token is None:
                break


gdm: GoogleDriveMonitor = GoogleDriveMonitor()


class WebHook(BaseHTTPRequestHandler):
    global gdm

    def _set_headers(self, code):
        self.send_response(code)
        self.send_header("Content-type", "json")
        self.end_headers()

    def do_GET(self):
        self._set_headers(200)
        self.wfile.write("It's working".encode('utf-8'))

    def do_POST(self):
        self._set_headers(200)
        # length = int(self.headers.get("content-length"))
        # if length:
        #     r = self.rfile.read(length)
        #     params = parse_qs(r.decode("utf-8"))
        #     print(params)

        # print(str(self.headers))
        if int(self.headers.get('X-Goog-Message-Number')) != 1:
            gdm.review_changes()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Must provide Ngrok API token")
        sys.exit(1)

    ngrok_token = sys.argv[1]

    with HTTPServer(('127.0.0.1', 0), WebHook) as server:
        ngrok.set_auth_token(ngrok_token)
        listener = ngrok.forward(server.server_port)

        # Output ngrok url to console
        print(f"Ingress established at {listener.url()}")

        gdm.register_hook(listener.url())
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            gdm.unregister_hook()
