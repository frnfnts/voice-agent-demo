from google.oauth2 import service_account
from googleapiclient.discovery import build


def export_doc(file_id: str, sa_info: dict, mime_type: str) -> bytes:
    credentials = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    request = drive.files().export(fileId=file_id, mimeType=mime_type)
    return request.execute()
