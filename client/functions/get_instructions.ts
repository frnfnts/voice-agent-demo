import { JWT } from 'google-auth-library';

export const onRequest = async (context: any) => {
  const fileId = context.env.VITE_GDRIVE_INSTRUCTION_FILE_ID;
  const credentials = JSON.parse(context.env.VITE_KEYFILE_JSON);

  const client = new JWT({
    email: credentials.client_email,
    key: credentials.private_key,
    scopes: ['https://www.googleapis.com/auth/drive.readonly'],
  });

  try {
    const tokenResponse = await client.authorize();
    const url = `https://www.googleapis.com/drive/v3/files/${fileId}/export?mimeType=text%2Fplain`;

    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${tokenResponse.access_token}` },
    });

    const text = await response.text();

    return new Response(text, {
      headers: {
        'Content-Type': 'text/plain; charset=UTF-8',
        'Access-Control-Allow-Origin': '*' // 必要に応じて
      }
    });
  } catch (err: any) {
    return new Response(err.message, { status: 500 });
  }
}
