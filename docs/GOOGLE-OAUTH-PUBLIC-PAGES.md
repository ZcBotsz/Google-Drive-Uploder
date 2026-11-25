# Public pages for Google OAuth

The public routes below do not require a Studio session:

- Home: `/home`
- Privacy Policy: `/privacy`
- Terms of Service: `/terms`

For the existing Render hostname, after deployment the full URLs are:

- `https://google-drive-uploader-1.onrender.com/home`
- `https://google-drive-uploader-1.onrender.com/privacy`
- `https://google-drive-uploader-1.onrender.com/terms`

The homepage links to both legal pages; the sign-in page also links to them.
The Studio landing behavior, bot, Admin and existing OAuth flows are unchanged.
Deploy and visit each URL without signing in to confirm it is publicly reachable.

## Before submitting to Google

1. **Review the privacy policy and terms with the service operator** and edit them
   to match the real operation, legal entity, support arrangements, retention,
   infrastructure, disclosure, and deletion process. These texts are starting
   drafts, not legal advice or a guarantee of approval. Publish only statements
   you can actually honor.
2. In particular, the code can send transfer metadata (including Drive IDs or
   links) to a configured Telegram log channel, and can copy Telegram source
   messages/files to a configured dump channel. Review those settings, access,
   disclosures, consent and Google API Services User Data Policy / Limited Use
   compliance before offering this to users. A privacy-policy statement alone
   does not establish compliance.
3. Google requires a publicly accessible homepage and policy and may require
   verification of domain ownership for OAuth review. `onrender.com` is a
   shared hosting domain. Google may reject its use for domain verification or
   request additional provider justification. If so, configure an owned custom
   domain on Render, verify it through Google Search Console, and use that domain
   for all public pages and the matching OAuth callback/client configuration.
   Do not claim ownership of the hosting provider's domain.
4. In Google Auth Platform, enter the appropriate homepage, policy and terms URLs.
   Configure a suitable External audience and publish/submit for verification as
   Google requires for the actual requested scopes. The default project scope is
   `drive.file`; full Drive scope has different access and review requirements.
5. For this service's current hostname, the project's OAuth callback is normally
   `https://google-drive-uploader-1.onrender.com/auth/google/callback`. Confirm
   that the Google Cloud web client and deployed `GOOGLE_REDIRECT_URI` match
   exactly. Do not publish credentials or secrets.

No Google Cloud, domain, or Render settings are changed by this package.
