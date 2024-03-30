# GoogleDriveMonitor

## Enable Google Drive API

On Google cloud console [enable](https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com) the Google drive API and activity API .
And download the token JSON to the tool directory with the name `credentials.json`.

## Install and Run

Install the python dependencies using the command  `pip install -r requirements.txt` and run the `main.py` with the `ngrok` auth token as follow `python main.py <ngrok token>`.

The output will be a list of changes for every new file uploaded that inherits an `anyone` permission type. 