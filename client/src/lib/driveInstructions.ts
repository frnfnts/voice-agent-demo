import { JWT } from 'google-auth-library';

export async function getDocContentAsString(fileId: string, keyFileJson: string): Promise<string> {
  const credentials = JSON.parse(keyFileJson);

  // JWT（サービスアカウント認証）の作成
  const client = new JWT({
    email: credentials.client_email,
    key: credentials.private_key,
    scopes: ['https://www.googleapis.com/auth/drive.readonly'],
  });

  try {
    // アクセストークンの取得
    const tokenResponse = await client.authorize();
    const accessToken = tokenResponse.access_token;

    const mimeType = 'text/plain';

    // 標準の fetch を使って Google Drive API を叩く
    const url = `https://www.googleapis.com/drive/v3/files/${fileId}/export?mimeType=${encodeURIComponent(mimeType)}`;

    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Google API Error: ${errorText}`);
    }

    return await response.text();

  } catch (error: any) {
    throw new Error(error.message);
  }
}
