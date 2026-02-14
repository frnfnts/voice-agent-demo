#!/usr/bin/env python3
"""chat_logs ディレクトリのログを Google Drive にアップロードする。

Drive 側に同名ファイルが既に存在するものはスキップし、
存在しないログだけをアップロードする。

Usage:
  python upload_logs.py                     # chat_logs/ 内の未アップロード分を同期
  python upload_logs.py --dry-run           # アップロード対象の確認のみ（実行しない）
  python upload_logs.py --folder-id <ID>    # アップロード先フォルダを指定
"""

import argparse
import json
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# --- Configuration ---
SERVICE_ACCOUNT_PATH = Path(__file__).parent / "ame-ai-agent.json"
LOG_DIR = Path(__file__).parent / "chat_logs"
DEFAULT_FOLDER_ID = "1rmeUlt3rjX9RzK9S9FdG1mTx1B-8dpAE"

# drive.file スコープ: このアプリが作成・開いたファイルのみ操作可能
# drive スコープ: フォルダ内一覧取得にも必要
SCOPES = ["https://www.googleapis.com/auth/drive"]


def get_drive_service():
    """サービスアカウントで認証し Drive API クライアントを返す。"""
    if not SERVICE_ACCOUNT_PATH.exists():
        print(f"❌ Error: {SERVICE_ACCOUNT_PATH} が見つかりません", file=sys.stderr)
        sys.exit(1)

    with open(SERVICE_ACCOUNT_PATH, "r", encoding="utf-8") as f:
        sa_info = json.load(f)

    credentials = service_account.Credentials.from_service_account_info(
        sa_info, scopes=SCOPES
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def list_existing_files(drive, folder_id: str) -> set[str]:
    """Drive フォルダ内の既存ファイル名一覧を取得する。"""
    existing = set()
    page_token = None

    while True:
        response = (
            drive.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(name)",
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )

        for f in response.get("files", []):
            existing.add(f["name"])

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return existing


def get_local_logs() -> list[Path]:
    """chat_logs ディレクトリ内の JSON ファイル一覧を取得する。"""
    if not LOG_DIR.exists():
        print(f"❌ Error: {LOG_DIR} が見つかりません", file=sys.stderr)
        sys.exit(1)

    return sorted(LOG_DIR.glob("*.json"))


def upload_file(drive, file_path: Path, folder_id: str) -> str:
    """ファイルを Google Drive にアップロードし、ファイルIDを返す。"""
    file_metadata = {
        "name": file_path.name,
        "parents": [folder_id],
    }
    media = MediaFileUpload(
        str(file_path), mimetype="application/json", resumable=True
    )
    uploaded = (
        drive.files()
        .create(
            body=file_metadata,
            media_body=media,
            fields="id, name",
            supportsAllDrives=True,
        )
        .execute()
    )
    return uploaded["id"]


def main():
    parser = argparse.ArgumentParser(
        description="chat_logs を Google Drive にアップロード（差分のみ）"
    )
    parser.add_argument(
        "--folder-id",
        default=DEFAULT_FOLDER_ID,
        help=f"アップロード先の Google Drive フォルダID (default: {DEFAULT_FOLDER_ID})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="アップロード対象を表示するだけで実行しない",
    )
    args = parser.parse_args()

    print("🔑 Google Drive に接続中...")
    drive = get_drive_service()

    print(f"📂 Drive フォルダの既存ファイルを取得中...")
    existing = list_existing_files(drive, args.folder_id)
    print(f"   Drive 上のファイル数: {len(existing)}")

    local_logs = get_local_logs()
    print(f"   ローカルのログ数: {len(local_logs)}")

    # 差分を計算
    to_upload = [f for f in local_logs if f.name not in existing]

    if not to_upload:
        print("\n✅ すべてのログは既にアップロード済みです。")
        return

    print(f"\n📤 アップロード対象: {len(to_upload)} ファイル")
    for f in to_upload:
        print(f"   - {f.name}")

    if args.dry_run:
        print("\n🔍 --dry-run のため、アップロードは実行しません。")
        return

    print()
    success = 0
    for i, f in enumerate(to_upload, 1):
        try:
            file_id = upload_file(drive, f, args.folder_id)
            print(f"   [{i}/{len(to_upload)}] ✅ {f.name} → {file_id}")
            success += 1
        except Exception as e:
            print(f"   [{i}/{len(to_upload)}] ❌ {f.name}: {e}")

    print(f"\n🎉 完了: {success}/{len(to_upload)} ファイルをアップロードしました。")


if __name__ == "__main__":
    main()
