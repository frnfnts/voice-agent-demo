import { google } from 'googleapis';
import fs from 'fs';

/**
 * Google ドキュメントの内容を文字列として取得する
 */
export async function getDocContentAsString(fileId: string, keyFileJson: string): Promise<string> {
  const serviceAccount = JSON.parse(keyFileJson);
  const auth = new google.auth.GoogleAuth({
    credentials: {
      client_email: serviceAccount.client_email,
      private_key: serviceAccount.private_key,
    },
    scopes: ['https://www.googleapis.com/auth/drive.readonly'],
  });

  const drive = google.drive({ version: 'v3', auth });

  try {
    const response = await drive.files.export({
      fileId: fileId,
      mimeType: 'text/plain',
    });

    // response.data にテキスト内容が文字列として入っています
    const content = response.data as string;

    return content;
  } catch (error) {
    console.error('テキスト取得エラー:', error);
    throw error;
  }
}
