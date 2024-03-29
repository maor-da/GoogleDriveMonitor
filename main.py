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


class GoogleDriveMonitor:
    SCOPES = ["https://www.googleapis.com/auth/drive"]
    saved_token_file = "saved_start_page_token.json"

    def __init__(self):
        self.resource_id = None
        self.hook_id = None
        self.saved_start_page_token = None
        self.drive = None
        self.creds = None

    def connect(self):
        if self.creds is None:
            if os.path.exists("token.json"):
                self.creds = Credentials.from_authorized_user_file("token.json", self.SCOPES)
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and self.creds.refresh_token:
                    self.creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        "credentials.json", self.SCOPES
                    )
                    #  Open SSO authentication
                    self.creds = flow.run_local_server(port=0)
                # Save the credentials for the next run
                with open("token.json", "w") as token:
                    token.write(self.creds.to_json())

        if self.drive is None:
            self.drive = build("drive", "v3", credentials=self.creds)

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
        if self.hook_id and self.resource_id:
            # Prevent multiple registrations
            return

        try:
            self.connect()
            self.hook_id = str(uuid.uuid4())
            self.get_start_page_token()

            folders = []
            page_token = None
            while True:
                response = (
                    self.drive.files()
                    .list(
                        q="mimeType='application/vnd.google-apps.folder'",
                        spaces="drive",
                        fields="nextPageToken, files(id, name, permissionIds)",
                        pageToken=page_token,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True
                    )
                    .execute()
                )
                for file in response.get("files", []):
                    # Process change
                    print(f'Found file: {file.get("name")}, {file.get("id")}')
                folders.extend(response.get("files", []))
                page_token = response.get("nextPageToken", None)
                if page_token is None:
                    break

            for folder in folders:
                body = {
                    "id": self.hook_id,
                    "type": "web_hook",
                    "address": hook_url
                }
            response = self.drive.changes().watch(body=body, pageToken=self.saved_start_page_token,
                                                  includeItemsFromAllDrives=True, supportsAllDrives=True).execute()

            self.resource_id = response.get("resourceId")
            print(response)
            #     body = {
            #         "id": self.hook_id,
            #         "type": "web_hook",
            #         "address": hook_url
            #     }
            # response = self.drive.changes().watch(body=body, pageToken=self.saved_start_page_token,
            #                                       includeItemsFromAllDrives=True, supportsAllDrives=True).execute()
            #
            # self.resource_id = response.get("resourceId")
            # print(response)
            # r = service.channels().stop(body={"id": hook_id, "resourceId": response["resourceId"]}).execute()

            # # Call the Drive v3 API
            # results = (
            #     service.files()
            #     .list(pageSize=10, fields="nextPageToken, files(id, name)")
            #     .execute()
            # )
            # items = results.get("files", [])
            #
            # if not items:
            #     print("No files found.")
            #     return
            # print("Files:")
            # for item in items:
            #     print(f"{item['name']} ({item['id']})")
        except HttpError as error:
            print(f"An error occurred: {error}")

    def unregister_hook(self):
        if self.hook_id and self.resource_id:
            self.drive.channels().stop(body={"id": self.hook_id, "resourceId": self.resource_id}).execute()
            self.resource_id = None
            self.hook_id = None

    def review_changes(self):
        page_token = self.saved_start_page_token
        while page_token is not None:
            response = self.drive.changes().list(pageToken=page_token, spaces="drive",
                                                 includeItemsFromAllDrives=True, supportsAllDrives=True,
                                                 fields="nextPageToken, newStartPageToken, changes").execute()

            for change in response.get("changes"):
                print(json.dumps(change, indent=4))
                try:
                    permissions = self.drive.permissions().list(fileId=change.get("fileId")).execute()
                    print(json.dumps(permissions, indent=4))
                    file = self.drive.files().get(fileId=change.get("fileId")).execute()
                    print(json.dumps(file, indent=4))
                except HttpError as error:
                    print(f"An error occurred: {error}")
            if "newStartPageToken" in response:
                # On the last page we save the token
                self.saved_start_page_token = response.get("newStartPageToken")
                self.save_start_page_token()
            page_token = response.get("nextPageToken")


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
        length = int(self.headers.get("content-length"))
        if length:
            r = self.rfile.read(length)
            params = parse_qs(r.decode("utf-8"))
            print(params)

        # print(str(self.headers))
        if int(self.headers.get('X-Goog-Message-Number')) != 1:
            gdm.review_changes()

        # length = int(self.headers.get("content-length"))
        # r = self.rfile.read(length)
        # params = parse_qs(r.decode("utf-8"))
        # print(params)
        # a = self.headers.get("X-Goog-Resource-Uri")
        # params = parse_qs(urlparse(a).query)
        # if 'pageToken' in params:
        #     print(params)


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
