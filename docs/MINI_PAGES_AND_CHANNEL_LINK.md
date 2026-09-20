# Mini pages + channel linking

## Mini pages (not a full website product)

1. Tutor calls `create_html_page` with title + content.
2. HTML is stored as an artifact.
3. Tool returns `page_url` = `{PUBLIC_BASE_URL}/pages/{id}?token=...`
4. Learner opens the link in a browser — HTML is served by the **web** service.

Requires `PUBLIC_BASE_URL` = your Railway web URL.

## Channel link (OTP)

1. Learner on Telegram: "I also use WhatsApp +234..."
2. Tutor: `request_channel_link(target_channel=whatsapp, target_external_id=234...)`
3. WAX sends a 6-digit code **to that WhatsApp**.
4. Learner pastes code in Telegram.
5. Tutor: `confirm_channel_link(code=123456)`
6. Both channel identities point at the same Principal.

Fallback: `method=knowledge` if OTP cannot be delivered.
