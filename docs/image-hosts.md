# Image hosts

Upload-Assistant keeps image-host API metadata in `src/image_hosts.py`. The
registry is the source of truth for supported host names, configuration keys,
known upload endpoints, provider documentation, and documented upload-size
limits. Screenshot validation and the config generator consume that same
registry so those values do not drift independently.

The contracts below were re-audited on **2026-08-18**. Public documentation is
linked where the provider publishes an API. Private/custom integrations are
called out explicitly instead of being presented as if they had a public API
specification.

| Host | Contract used by Upload-Assistant | Authentication | Provider documentation / status |
| --- | --- | --- | --- |
| `imgbb` | ImgBB API v1, `POST /1/upload`, multipart `image` | `key` query parameter | <https://api.imgbb.com/> |
| `imgbox` | ImgBox web uploader through `pyimgbox` | none | <https://imgbox.com/> — no public upload API specification; the site currently reports an extended service disruption |
| `pixhost` | PiXhost API v2, `POST /images`, multipart `img` | none | <https://pixhost.to/api/index.html> |
| `lensdump` | Lensdump/Chevereto API v1.1, multipart `source` | `X-API-Key` | <https://lensdump.com/page/api-doc> |
| `ptscreens` | Chevereto API v1.1, multipart `source` | `X-API-Key` | <https://ptscreens.com/api-v1> |
| `onlyimage` | Chevereto API v1.1, multipart `source` | `X-API-Key` | <https://onlyimage.org/api-v1> |
| `utppm` | Chevereto API v1, multipart `source` | `X-API-Key` | <https://v4-docs.chevereto.com/api/1/file-upload.html> |
| `passtheimage` | Chevereto API v1, multipart `source` | `X-API-Key` | <https://v4-docs.chevereto.com/api/1/file-upload.html> |
| `dalexni` | Private ImgBB-compatible contract | configured key | No public API document was found; the endpoint is treated as private and failures are surfaced/fallback immediately |
| `zipline` | Zipline `POST /api/upload`, multipart `file` | `Authorization` token | <https://zipline.diced.sh/docs/api> |
| `midnightscene` | Zipline-compatible fixed endpoint | `Authorization` token | Zipline API documentation above |
| `seedpool_cdn` | Seedpool private upload endpoint | Bearer token | No public API specification was found |
| `sharex` | User-configured ShareX-style endpoint | configured token | Contract depends on the configured server; the default DigitalCore endpoint publishes its ShareX JSON at <https://img.digitalcore.club/> |
| `lostimg` | LostImg private API | Bearer token | <https://lostimg.cc/docs/api> exists but requires a signed-in account; unauthenticated endpoint behavior was verified live |

## Live audit snapshot (2026-08-18)

The audit used non-destructive requests and did not require storing repository
credentials. A `400`/`401` authentication response is considered a healthy
protocol-level result when the endpoint correctly rejected a missing/invalid
key.

- **ImgBB:** API v1 reachable; missing/invalid key returns structured JSON `400` as documented. A successful credentialed upload could not be tested without a local API key.
- **ImgBox:** token/gallery creation is reachable, but a real generated PNG/JPEG upload returns HTTP `500`. The homepage independently reports an extended provider-side service disruption. The latest published `pyimgbox` is still 1.0.7 (2023). Upload-Assistant now falls through immediately to the next host on this condition.
- **PiXhost:** live anonymous API v2 upload succeeded. The returned thumbnail, public page, and derived full-size CDN URL were all verified; the full-size URL returned HTTP `200 image/png`.
- **Lensdump, PT Screens, OnlyImage, utp.pm, PassTheImage:** upload endpoints return the expected structured Chevereto/Lensdump `400 Invalid API key` response to an invalid audit key. PT Screens and OnlyImage publish API v1.1 pages directly; utp.pm and PassTheImage expose Chevereto but require login for their local API page.
- **OnlyImage operational note:** its homepage currently states that all API keys must be regenerated. A previously working key may therefore need replacement in `onlyimage_api`.
- **Dalexni:** the endpoint currently returns Cloudflare HTTP `403` from the audit environment, so a successful upload could not be verified. It is treated as a private contract and the uploader falls through on the non-JSON Cloudflare response.
- **MidnightScene:** its Zipline endpoint returns structured JSON `401` without a valid session/token, confirming the endpoint is reachable.
- **Seedpool CDN:** returns structured JSON `401` without authentication, confirming the endpoint is reachable.
- **Default ShareX/DigitalCore endpoint:** publishes a ShareX configuration whose method, `Authorization` header, multipart `file` field, `title` argument, and `data.link` result match the Upload-Assistant integration. The unauthenticated audit request returns structured JSON `401`.
- **LostImg:** publishes an API-docs route (account sign-in required) and returns structured JSON `401` for an invalid/missing API key.
- **Custom Zipline/ShareX instances:** availability is inherently instance-specific; their protocol is validated against the upstream Zipline contract or the configured ShareX JSON contract respectively.

## Documented size limits

- ImgBB: **32 MB** maximum image size.
- ImgBox: **10 MB** maximum image size; JPG/GIF/PNG are advertised by the site.
- PiXhost API v2: **10 MB** maximum image size; JPEG, PNG, GIF, WebP and AVIF are documented.
- Hosts without a public fixed maximum are not given an invented limit by the
  application. The provider remains authoritative and its API error is surfaced.

## Failure and fallback behavior

Image hosts are a prioritized list: `img_host_1` through `img_host_10`. A
provider/network 5xx failure marks the host unavailable for the current upload
manager and falls through to the next configured host. Configuration/authentication
errors and malformed 4xx requests are non-retryable on that host, avoiding three
identical attempts for every screenshot. Timeouts are not retried on the same
host because the remote service may have accepted the upload before the client
timed out.

ImgBox deserves special handling: the current `pyimgbox` release is still 1.0.7
and follows the same web-upload endpoints exposed by ImgBox, but live uploads
currently return HTTP 500 while ImgBox itself displays a service-disruption
notice. Upload-Assistant therefore treats that failure as host unavailability
and continues to the next configured image host instead of printing the remote
HTML error or repeatedly retrying it.

## API-key configuration

Credential names remain in `data/config.py` / `data/example_config.py`:

- `imgbb_api`
- `lensdump_api`
- `ptscreens_api`
- `onlyimage_api`
- `utppm_api`
- `passtheima_ge_api`
- `dalexni_api`
- `zipline_url` + `zipline_api_key`
- `midnightscene_api_key`
- `seedpool_cdn_api`
- `sharex_url` + `sharex_api_key`
- `lostimg_api`

ImgBox and PiXhost do not require a key for the upload contracts used here.
