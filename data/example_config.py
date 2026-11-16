# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
from typing import Any

config: dict[str, Any] = {
    "DEFAULT": {
        # --- MAIN SETTINGS ---
        # Set to True to display a notice when an update is available.
        "update_notification": True,
        # Set to True to display the changelog when an update is available.
        "verbose_notification": False,
        # Number of hours to reuse a successful update check. Set to 0 to check every run.
        "update_notification_cache_hours": 4,
        # tmdb api key **REQUIRED**
        # visit "https://www.themoviedb.org/settings/api" copy api key and insert below
        "tmdb_api": "",  # TMDb v3 API key (preferred for existing configs)
        "tmdb_access_token": "",  # Optional TMDb v4 API Read Access Token
        # METADATA CACHE
        # Temporarily saves responses from sites such as TMDB and IMDb. Future
        # runs can reuse this data and make fewer API requests.
        # Leave this as True in most cases. Use False only to test fresh data
        # or troubleshoot an outdated result.
        "metadata_cache_enabled": True,
        # Folder where cached data is saved. Relative paths are created inside
        # the Upload-Assistant folder. You normally do not need to change this.
        "metadata_cache_dir": "data/cache/metadata",
        # Number of hours a normal result can be reused.
        # 168 = 7 days. Lower this for fresher data, or raise it to reduce API
        # requests further.
        "metadata_cache_default_ttl_hours": 168,
        # Number of minutes a search with no result is remembered. This avoids
        # repeating a query that an API did not find. 60 = 1 hour.
        "metadata_cache_negative_ttl_minutes": 60,
        # Optional settings for each service. "enabled": False disables caching
        # only for that service. "ttl_hours" overrides the default above.
        # You can remove services from this list to use the default duration.
        "metadata_cache_services": {
            # Movies and TV Shows. "localized_ttl_hours" controls data in other
            # languages, such as a Portuguese overview and title.
            "tmdb": {
                "enabled": True,
                "ttl_hours": 168,
                "localized_ttl_hours": 168,
            },
            # Movie and series information identified by tt1234567.
            "imdb": {"enabled": True, "ttl_hours": 72},
            # Episode guides and show names.
            "tvdb": {"enabled": True, "ttl_hours": 168},
            "tvmaze": {"enabled": True, "ttl_hours": 24},
            # Anime information, including romaji titles and MAL IDs.
            "anilist": {"enabled": True, "ttl_hours": 168},
            "douban": {"enabled": True, "ttl_hours": 168},
            "thexem": {"enabled": True, "ttl_hours": 168},
            # Game information.
            "igdb": {"enabled": True, "ttl_hours": 168},
            "steam": {"enabled": True, "ttl_hours": 72},
            # Book, audiobook, and comic metadata. This data changes rarely,
            # so the default here is 30 days (720 hours).
            "google_books": {"enabled": True, "ttl_hours": 720},
            # Audible catalog metadata and user rating counts change more often.
            "audible": {"enabled": True, "ttl_hours": 24},
            # Work IDs, ISBNs, and author names from OpenLibrary.
            "openlibrary": {"enabled": True, "ttl_hours": 720},
            # Data fetched from a MAM account; a shorter duration reflects changes.
            "myanonamouse": {"enabled": True, "ttl_hours": 24},
            # Private-tracker game metadata can be edited, so keep this relatively fresh.
            "gazellegames": {"enabled": True, "ttl_hours": 24},
            # Music release metadata; it also uses 30 days because it changes rarely.
            "musicbrainz": {"enabled": True, "ttl_hours": 720},
            # XXX scene and movie metadata from ThePornDB and BLVCKOUT.
            "tpdb": {"enabled": True, "ttl_hours": 168},
            "blvckout": {"enabled": True, "ttl_hours": 168},
            "discogs": {"enabled": True, "ttl_hours": 720},
        },
        # Tracker-ID metadata cache
        # Cache tracker metadata when you provide a specific torrent ID, such as --ptp, --hdb,
        # or a tracker link in a torrent comment. Duplicate checks and broad searches are never cached.
        "tracker_metadata_cache_enabled": True,
        # Folder for cached tracker responses. You normally do not need to
        # change this setting.
        "tracker_metadata_cache_dir": "data/cache/tracker_metadata",
        # Number of hours an explicit tracker torrent-ID response can be reused.
        # 24 = 1 day. Trackers can edit descriptions and images, so this is kept
        # shorter than the public metadata cache.
        "tracker_metadata_cache_ttl_hours": 24,
        # Number of minutes an explicit tracker ID with no result is remembered.
        # 15 minutes avoids repeated requests without hiding a newly available
        # torrent for too long.
        "tracker_metadata_cache_negative_ttl_minutes": 15,
        # Google Books API key. "" = disabled
        # You can get one here https://console.cloud.google.com/apis/library/books.googleapis.com
        "google_books_api_key": "",
        # Google Cloud Translation Basic API key. "" = disabled
        # Used to translate non-English BOOK metadata for trackers that require English.
        # Enable the Cloud Translation API in the Google Cloud project before using it.
        "google_translate_api_key": "",
        # Twitch/IGDB API credentials. "" = disabled
        # You can get them from the Twitch Developer Console (https://dev.twitch.tv/console)
        "twitch_client_id": "",
        "twitch_client_secret": "",
        # MyAnonamouse (MAM) API key / session cookie (mam_id). "" = disabled
        # You can get it from Preferences > Security > View IP locked session cookie
        "mam_api_key": "",
        # tvdb api key
        # visit "https://www.thetvdb.com/api-information/signup" copy api key and insert below
        "tvdb_api": "",
        # visit "https://thetvdb.github.io/v4-api/#/Login/post_login" enter api key, generate token and insert token below
        # the pin in the login form is not needed (don't modify), only enter your api key
        "tvdb_token": "",
        # Play the bell sound effect when asking for confirmation
        "sfx_on_prompt": True,
        # Console logging configuration
        # Show time in console logs
        "console_show_time": False,
        # Show log level in console logs
        "console_show_level": False,
        # Show file path of log source in console logs
        "console_show_path": False,
        # Enable rich markup parsing in console logs
        "console_markup": True,
        # File logging configuration
        # Set true to save a plain text log file of the upload flow in each release's tmp folder
        "write_log": False,
        # Debug configuration
        # Set true to enable debug mode globally (same as running with --debug)
        "debug": False,
        # Console logging configuration when running in debug mode
        # Show time in console logs under debug mode
        "console_debug_show_time": True,
        # Show log level in console logs under debug mode
        "console_debug_show_level": True,
        # Show file path of log source in console logs under debug mode
        "console_debug_show_path": True,
        # Enable rich markup parsing in console logs under debug mode
        "console_debug_markup": True,
        # How many trackers need to pass successful checking to continue with the upload process
        # Default = 1. If 1 (or more) tracker/s pass banned_group, content and dupe checking, uploading will continue
        # If less than the number of trackers pass the checking, exit immediately.
        "tracker_pass_checks": 1,
        # Set true to suppress config warnings on startup
        "suppress_warnings": False,
        # Set true to embed terminal links using hyperlinks (OSC 8)
        # Set false to display the full raw URLs (recommended for terminals that do not support embedded links)
        "embed_links": True,
        # Set true to show size difference between duplicate and the upload in duplicate checks
        "show_dupe_size_diff": True,
        # Ignore duplicates if the size difference between the dupe and our upload is greater than or equal to this percentage
        # For example: 20 means a 20% or more difference in size (larger or smaller) will exclude the dupe
        # Set to None/null or remove to disable this feature.
        "dupe_size_difference_tolerance": None,
        # NOT RECOMMENDED UNLESS YOU KNOW WHAT YOU ARE DOING.
        # Will prevent meta.json file from being deleted before running
        "keep_meta": False,
        # IMAGE HOSTING SETTINGS
        # Order of image hosts. primary host as first with others as backup
        # Available image hosts: imgbb, imgbox, pixhost, lensdump, ptscreens, onlyimage, dalexni, zipline, midnightscene, passtheimage, seedpool_cdn, sharex, utppm, lostimg
        "img_host_1": "",
        "img_host_2": "",
        "img_host_3": "",
        "img_host_4": "",
        "img_host_5": "",
        "img_host_6": "",
        "img_host_7": "",
        "img_host_8": "",
        "img_host_9": "",
        "img_host_10": "",
        # Prefer one configured host accepted by every selected tracker that
        # declares an image-host policy. If none is shared, use per-tracker
        # fallback hosting as usual.
        "smart_image_host_selection": True,
        # Maximum number of image uploads running at once. Set to 0 to use host defaults.
        "image_upload_concurrency": 0,
        # Delay between starting image uploads, in seconds.
        "image_upload_delay": 0.0,
        # Minimum number of successful image uploads required to continue.
        "min_successful_image_uploads": "3",
        # Image-host credentials
        # Optional Catbox userhash. Leave blank for anonymous uploads.
        "catbox_userhash": "",
        "dalexni_api": "",
        "imgbb_api": "",
        "lensdump_api": "",
        "lostimg_api": "",
        # LuminaPix (luminapix.eu) API key, the only image host Kufirc accepts.
        "luminapix_api": "",
        "onlyimage_api": "",
        "passtheima_ge_api": "",
        "ptscreens_api": "",
        # MidnightScene (Zipline) API key. Sign in, click your avatar, then choose
        # "Copy token" (or open Settings > User). Never share or commit this token.
        "midnightscene_api_key": "",
        # Seedpool CDN API key
        "seedpool_cdn_api": "",
        # ShareX-style image host (IMageHosting) token
        "sharex_url": "https://img.digitalcore.club/api/upload",
        "sharex_api_key": "",
        "thrimg_api": "",
        # utp.pm API key
        "utppm_api": "",
        # GETTING METADATA
        # btn api key used to get details from btn
        "btn_api": "",
        # set true to skip automated client torrent searching
        # this will search qbittorrent clients for matching torrents
        # and use found torrent id's for existing hash and site searching
        "skip_auto_torrent": False,
        # Set to true to always just use the largest playlist on a blu-ray, without selection prompt.
        "use_largest_playlist": False,
        # Import policy for releases found on other trackers. Choose one:
        # - "ids": import only IMDb/TMDb/TVDb/MAL and related release metadata.
        # - "images": import IDs/metadata and validated description screenshots, but no text.
        # - "text": import IDs/metadata and cleaned description text, but no screenshots.
        # - "text_and_images": import IDs/metadata, cleaned text, and validated screenshots.
        # Use --onlyID to temporarily force "ids" for one execution.
        "tracker_description_mode": "text",
        # Maximum number of tracker-ID metadata candidates queried at once.
        "tracker_search_concurrency": 4,
        # Set the full path to the `ebook-meta` binary used by zentag's ebook mode.
        # Leave blank to resolve from system PATH.
        "ebook_meta_path": "",
        # Only query tracker metadata when a torrent ID is known from a client comment or --tracker-id.
        "tracker_comment_only": True,
        # set true to use argument overrides from data/templates/user-args.json
        "user_overrides": False,
        # Automatically set --personalrelease to True if the detected release group matches any of these tags (case-insensitive)
        "personal_release_groups": [],
        # If there is no region/distributor ids specified, we can use existing torrents to check
        # This will use data from matching torrents in qBitTorrent/RuTorrent to find matching site ids
        # and then try and find region/distributor ids from those sites
        # Requires "skip_auto_torrent" to be set to False
        "ping_unit3d": False,
        # If processing a dvd/bluray disc, get related information from bluray.com
        # This will set region and distribution info
        # Must have imdb id to work
        "get_bluray_info": False,
        # A release with 100% score will have complete matching details between bluray.com and bdinfo
        # Each missing Audio OR Subtitle track will reduce the score by 5
        # Partial matched audio tracks have a 2.5 score penalty
        # If only a single bdinfo audio/subtitle track, penalties are doubled
        # Video codec/resolution and disc size mismatches have huge penalties
        # Only useful in unattended mode. If not unattended you will be prompted to confirm release
        # Final score must be greater than this value to be considered a match
        # Only works with blu-ray discs, not dvd
        "bluray_score": 94.5,
        # If there is only a single release on bluray.com, you may wish to relax the score a little
        "bluray_single_score": 89.5,
        # Set true to also try searching predb for scene release
        # predb is not consistent, can timeout, but can find some releases not found on SRRDB
        "check_predb": False,
        # SCREENSHOT HANDLING
        # Number of screenshots to capture
        "screens": "4",
        # XXX releases generate one contact sheet per video instead of individual frames.
        # Rows are vertical and columns are horizontal: 12 x 5 produces 60 thumbnails.
        "xxx_contact_sheet_rows": "12",
        "xxx_contact_sheet_columns": "5",
        # Maximum number of video files for which to generate XXX contact sheets.
        "xxx_contact_sheet_max_videos": "6",
        # Create XXX contact sheets as 5-second animated WebP files instead of PNG.
        "xxx_contact_sheet_animated_webp": False,
        "xxx_contact_sheet_animation_seconds": "5",
        # Set true to automatically capture DVD menu screenshots from menu VOBs
        "auto_dvd_menus": True,
        # Keep screenshots at the coded dimensions reported by MediaInfo.
        # Set true only to convert non-square-pixel video for display geometry;
        # this can change dimensions such as 1920x1040 to 1924x1040.
        "scale_screenshots_for_par": False,
        # Max number of disc menu screenshots to upload
        "max_menu_screens": "6",
        # Minimum successful image uploads required to continue
        "min_successful_image_uploads": "3",
        # Number of cutoff screenshots
        # If there are at least this many screenshots already, perhaps pulled from existing
        # description, skip creating and uploading any further screenshots.
        "cutoff_screens": "4",
        # Keep screenshots at the coded dimensions reported by MediaInfo.
        # Set to True only to convert non-square-pixel video to its display geometry.
        # This can change dimensions such as 1920x1040 to 1924x1040.
        "scale_screenshots_for_par": False,
        # Apply PAR scaling to DVD screenshots and DVD menu captures by default.
        # Set to False to keep their coded dimensions instead.
        "scale_dvd_screenshots_for_par": True,
        # Maximum number of FFmpeg processes that can run at once.
        # The effective limit is the lower of this value and the number of screenshots.
        "process_limit": "4",
        # Set to True to reduce CPU usage by applying an additional FFmpeg limit.
        "ffmpeg_limit": False,
        # FFmpeg compression level for screenshots (0-9).
        # A value of 6 provides a good balance between compression and speed.
        "ffmpeg_compression": "6",
        # Optional paths to external media tools. Leave blank to use the bundled
        # tool (when available) or the system PATH. MediaInfo DVD must remain
        # on 23.04 because newer versions do not preserve its DVD parsing.
        "ffmpeg_path": "",
        "ffprobe_path": "",
        "mediainfo_path": "",
        "dvd_mediainfo_path": "",
        "bdinfo_path": "",
        "mkbrr_path": "",
        "dovi_tool_path": "",
        "hdr10plus_tool_path": "",
        # Optional path to the unRAR executable for CBR/CBZ extraction.
        # Leave blank to use the system PATH.
        # Example: "C:\\Program Files\\WinRAR\\UnRAR.exe"
        "unrar_path": "",
        # Tonemap screenshots with the following settings (doesn't apply when using libplacebo)
        # See https://ayosec.github.io/ffmpeg-filters-docs/7.1/Filters/Video/tonemap.html
        "algorithm": "mobius",
        # Control desaturation of overly bright highlights when libplacebo is disabled.
        # Higher values preserve more color; lower values fade highlights toward white.
        # Set to 0.0 to disable. FFmpeg defaults to 2.0; this example uses 10.0.
        # Works only when the input frame has a supported color tag.
        "desat": "10.0",
        # Screenshot overlays
        # Layout examples and manual setup: docs/screenshot-overlays.md
        # Show enabled screenshot labels without changing the individual selections below.
        # Enable at least one label below to display overlay text.
        # Enabling overlays uses FFmpeg tone mapping instead of libplacebo.
        "frame_overlay": False,
        # Overlay text size (1-100), scaled with resolution. VapourSynth rounds to whole font-scale steps.
        "overlay_text_size": "18",
        # Keep labels at the top, aligned to "left" or "right".
        "overlay_position": "left",
        # Use "stacked" for one label per line, or "single_line" for compact labels separated by bullets.
        "overlay_layout": "stacked",
        # Show the estimated frame number at the capture position.
        "overlay_frame_number": False,
        # Show the frame's picture type (I, P, or B).
        "overlay_frame_type": False,
        # Show the captured frame's elapsed video time (HH:MM:SS.mmm).
        "overlay_timestamp": False,
        # Show only when screenshots have been tonemapped to SDR.
        "overlay_tonemapped": False,
        # --- DISC MENU SCREENSHOTS ---
        # Set to True to capture DVD menu screenshots from menu VOBs.
        "auto_dvd_menus": True,
        # Maximum number of disc menu screenshots to upload.
        "max_menu_screens": "6",
        # --- XXX CONTACT SHEETS ---
        # XXX releases generate one contact sheet per video instead of individual frames.
        # Rows are vertical and columns are horizontal: 12 x 5 produces 60 thumbnails.
        "xxx_contact_sheet_rows": "12",
        "xxx_contact_sheet_columns": "5",
        # Maximum number of video files for which to generate XXX contact sheets.
        "xxx_contact_sheet_max_videos": "6",
        # Set to True to create XXX contact sheets as animated WebP files instead of PNG.
        "xxx_contact_sheet_animated_webp": False,
        # Animation duration in seconds.
        "xxx_contact_sheet_animation_seconds": "5",
        # Add this many normal screenshots to the contact sheet for a single-video XXX release.
        "xxx_single_file_screens": "0",
        # --- GENERAL DESCRIPTION SETTINGS ---
        # See detailed documentation on how these settings affect description building:
        # https://github.com/wastaken7/Upload-Assistant/blob/development/docs/description-builder.md
        # Add a TMDB show or movie logo to the top of the description.
        "add_logo": True,
        # Logo width in pixels.
        "logo_size": "300",
        # Preferred logo language (ISO 639-1). Defaults to English ("en").
        # Falls back to English when a logo in the preferred language is unavailable.
        "logo_language": "",
        # Audible marketplace used to link ASINs in descriptions. Leave empty
        # unless you set your marketplace explicitly.
        # Examples: audible.com, audible.co.uk, audible.com.br.
        # --audible-url overrides this value for an individual upload. Audiobook
        # catalog lookup requires this domain (or --audible-url) and an ASIN.
        "audible_domain": "",
        # Screenshot thumbnail width where supported. Default: 350 (for example, [img=350]).
        "thumbnail_size": "350",
        # Number of screenshots per row on sites that use the common description.
        # Leave blank to use the default of 2.
        "screens_per_row": "",
        # Set to True to add the episode overview to the description.
        "episode_overview": True,
        # --- PACK DESCRIPTIONS ---
        # Number of screenshots to use for each disc or episode in packs on supported sites.
        # Set to 0 to use only the original description and images for later items.
        # PassThePopcorn always uses at least 2 images per item, regardless of this value.
        "multiScreens": "2",
        # The following pack settings do not affect PassThePopcorn, which uses a fixed format.
        # Screenshot thumbnail width for pack descriptions. Default: 300.
        "pack_thumb_size": "300",
        # Description character-count cutoff (including BBCode) for UNIT3D season packs.
        # After reaching this limit, only filenames and screenshots are used for any
        # additional files still to be added. Set a small value, such as 50, to include
        # only filenames and screenshots for each file, without MediaInfo.
        # UNIT3D sites enforce hard description limits. A little over 17,000 characters
        # worked in an AITHER forum post. If the current character count is below
        # charLimit, the next full MediaInfo block is added before the cutoff is applied.
        "charLimit": "14000",
        # How many files in a season pack are added before using an additional spoiler tag.
        # Files past this limit are grouped within the additional spoiler tag.
        "fileLimit": "2",
        # Maximum number of files processed for screenshots and MediaInfo in a pack.
        # You may not want to process screenshots and MediaInfo for 40 episodes in a season pack.
        "processLimit": "10",
        # --- DESCRIPTION HEADERS AND OVERRIDES ---
        # Header added to the top of the description where supported.
        # Can be overridden per tracker by adding the same setting to its configuration.
        "custom_description_header": "",
        # Header added above the screenshot section where supported.
        # Can be overridden per tracker by adding the same setting to its configuration.
        "screenshot_header": "[h2]Screenshots[/h2]",
        # Hide a lone generated image-section header; logos, text and signatures do not count.
        # Set to False to always keep image-section headers.
        "hide_screenshot_header_if_only_section": True,
        # Header added above screenshots after HDR tone mapping.
        # Can be overridden per tracker by adding the same setting to its configuration.
        "tonemapped_header": "[center]Screenshots have been adapted for SDR viewing, for reference only.[/center]",
        # Applicable only to raw discs (Blu-ray/DVD).
        # Header added above disc menu screenshots where supported.
        # Can be overridden per tracker by adding the same setting to its configuration.
        "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
        # Header added above audio spectrograms.
        "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
        # Header to add above Dolby Vision/HDR10+ metadata plots.
        "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
        # Allows adding a custom signature, in BBCode, at the bottom of the description section
        # Can be overridden in a per-tracker setting by adding this same config
        "custom_signature": "",
        # Override description text fields for specific release groups. Tags are matched
        # case-insensitively, with or without their leading hyphen.
        # Per-tracker tag_overrides take precedence over these DEFAULT overrides.
        "tag_overrides": {
            "MyAwesomeGroupTag": {
                "custom_description_header": "[center]MyAwesomeGroupTag release[/center]",
                "screenshot_header": "[h2]MyAwesomeGroupTag Screenshots[/h2]",
                "disc_menu_header": "[h2]MyAwesomeGroupTag Disc Menu Screenshots[/h2]",
                "audio_spectrogram_header": "[h2]MyAwesomeGroupTag Audio Spectrogram[/h2]",
                "dynamic_hdr_plot_header": "[h2]MyAwesomeGroupTag Dynamic HDR Metadata[/h2]",
                "tonemapped_header": "[center]MyAwesomeGroupTag SDR reference screenshots[/center]",
                "custom_signature": "[center]MyAwesomeGroupTag signature[/center]",
            },
        },
        # Add bluray.com link to description
        # Requires "get_bluray_info" to be set to True
        "add_bluray_link": True,
        # Set to True to add available Blu-ray.com cover, back, and slip images.
        # Requires "get_bluray_info" to be set to True.
        "use_bluray_images": True,
        # Width of Blu-ray.com cover images in pixels. BBCode limits image width, and
        # covers are usually taller than screenshots, so a smaller value is preferable.
        "bluray_image_size": "250",
        # Set true to add audio spectrograms to the description
        "add_audio_spectrogram": True,
        # Set true to generate and add Dolby Vision/HDR10+ dynamic metadata plots.
        # Required third-party tools are downloaded automatically on first use.
        # Warning: metadata extraction reads each selected video file in full and may take a while for large releases.
        "add_dynamic_hdr_plot": False,
        # Set true to generate spectrograms for all audio streams
        "process_all_audio_spectrogram": False,
        # Seconds from the beginning of each selected stream to analyse.
        "audio_spectrogram_duration": 600,
        # For music and audiobooks, limit the number of tracks or chapters processed.
        "audio_spectrogram_max_files": 12,
        # Limit plots generated for multi-file releases.
        "dynamic_hdr_plot_max_files": 1,
        # Maximum runtime in seconds for each HDR metadata tool invocation (capped at 7200).
        "dynamic_hdr_plot_tool_timeout": 3600,
        # CLIENT SETUP
        # Configures the order of uploads when both torrent trackers and Usenet are selected.
        # "concurrent" (default) -> Upload to Usenet and torrent trackers at the same time.
        # "usenet"               -> Upload to Usenet first. Torrent tracker uploads will start after Usenet upload finishes.
        # "tracker"              -> Upload to torrent trackers first. Once finished, we run the bandwidth check, and then start the Usenet upload.
        "upload_order": "concurrent",
        # Enable bandwidth control to wait for lower qBittorrent upload speed before uploading next tracker
        "qbit_bandwidth_control": False,
        # Threshold in KB/s. If the average upload speed is below this value, the next upload can start.
        "qbit_bandwidth_threshold": 0,
        # The duration of time in seconds to average the upload speed over.
        "qbit_bandwidth_time": 0,
        # Which client are you using.
        "default_torrent_client": "qbittorrent",
        # A list of clients to use for injection (aka actually adding the torrent for uploading)
        # eg: ["qbittorrent", "rtorrent"]
        # Will fallback to default_torrent_client if empty
        # "injecting_client_list": [""],
        # A list of clients to search for torrents.
        # eg: ["qbittorrent", "qbittorrent_searching"]
        # will fallback to default_torrent_client if empty
        # "searching_client_list": [""],
        # ARR* INTEGRATION SETTINGS
        # set true to use sonarr for tv show searching
        "use_sonarr": False,
        "sonarr_url": "http://localhost:8989",
        "sonarr_api_key": "",
        # details for a second sonarr instance
        # additional sonarr instances can be added by adding more sonarr_url_x and sonarr_api_key_x entries
        "sonarr_url_1": "http://my-second-instance:8989",
        "sonarr_api_key_1": "",
        # set true to use radarr for movie searching
        "use_radarr": False,
        "radarr_url": "http://localhost:7878",
        "radarr_api_key": "",
        # details for a second radarr instance
        # additional radarr instances can be added by adding more radarr_url_x and radarr_api_key_x entries
        "radarr_url_1": "http://my-second-instance:7878",
        "radarr_api_key_1": "",
        # TORRENT CREATION
        # set true to use mkbrr for torrent creation
        "mkbrr": True,
        # Number of mkbrr worker threads used for hashing (for example, 8).
        # Different values may improve performance. Use a lower value, such as 1, to reduce resource usage.
        # Set to 0 to choose the thread count automatically.
        "mkbrr_threads": "0",
        # Set true to prefer torrents with piece size <= 16 MiB when searching for existing torrents in clients
        "prefer_max_16_torrent": False,
        # Tracker based rehashing cooldown.
        # For trackers that might need specific piece size rehashing, using a value higher than 0 will add the specified cooldown
        # in (seconds) before rehashing begins, to allow other tasks to complete quickly, before resources are consumed by rehashing
        "rehash_cooldown": "0",
        # --- TRACKER CHECKS AND UPLOAD ---
        # Minimum number of trackers that must pass banned-group, content, and duplicate checks before uploading continues.
        # Default: 1. Upload Assistant exits if fewer trackers pass.
        "tracker_pass_checks": 1,
        # Set to True to show the size difference between a duplicate and the upload during duplicate checks.
        "show_dupe_size_diff": True,
        # Ignore duplicates when their size differs from the upload by at least this percentage.
        # For example, 20 excludes a duplicate that is at least 20% larger or smaller.
        # Set to None or remove this setting to disable the feature.
        "dupe_size_difference_tolerance": None,
        # Check MOVIE and TV duplicates against each tracker's slot rules, where Upload Assistant has them.
        # Shows a recommendation (upload / don't upload / review) with the date of the rules it is based on.
        # In unattended mode the recommendation decides whether to upload; when it can't decide, the basic check is used.
        # Default: True (also when this setting is missing).
        "slot_rules": True,
        # See docs/upload-order-and-bandwidth-control.md for workflows and qBittorrent requirements.
        "upload_order": "concurrent",
        "qbit_bandwidth_control": False,
        "qbit_bandwidth_control_after_usenet": False,
        "qbit_bandwidth_threshold": 0,
        "qbit_bandwidth_time": 0,
        # Minimum time between uploads when processing several releases in one run, such as a --queue.
        # Use seconds or a value such as "90s", "30m" or "1h30m". 0 disables it. --upload-interval overrides it.
        # The next release is prepared during the wait, and each tracker repeats its duplicate check before uploading.
        "upload_interval": 0,
        # Number of retry attempts after network or server errors, such as HTTP 500 responses or timeouts.
        # Can be overridden per tracker by adding the same setting to its configuration.
        "max_retries": 3,
        # Set to True to print how long each tracker upload takes.
        # This can help identify trackers that slow down the overall upload process.
        "show_upload_duration": True,
        # Set to True to print tracker API messages returned during uploads.
        "print_tracker_messages": False,
        # Set to True to print direct torrent links for uploaded content.
        "print_tracker_links": True,
        # --- POST-UPLOAD ---
        # Delay, in seconds, before injecting the torrent. This allows the tracker to register the hash and avoid "unregistered torrent" errors.
        # Can be overridden per tracker by adding the same setting to its configuration.
        "inject_delay": 0,
        # Set to False to disable adding cross-seed-suitable torrents found during duplicate checks.
        "cross_seeding": True,
        # Set to True to check every valid configured tracker for cross-seeding, even when not selected for upload.
        # Requires "cross_seeding" to be set to True.
        "cross_seed_check_everything": False,
        # Set to True to search for matching requests on supported trackers.
        "search_requests": True,
        # Optional trusted scripts from STATE_DIR/custom_hooks/ (Docker: /state/custom_hooks/) run after each item's tracker uploads and request search.
        # Scripts receive final metadata as JSON through stdin and may write status lines to the terminal.
        # Example: ["notify.py"]
        "post_upload_hooks": [],
        # Trusted hooks loaded into Upload Assistant's process. Each must expose an async or sync on_upload_finished(meta, config) function.
        # Each hook receives deep copies of the metadata and configuration.
        "post_upload_inprocess_hooks": [],
        # Maximum time, in seconds, allowed for each subprocess post-upload script.
        # Invalid or zero values use 30 seconds. A failed hook never changes the upload result.
        "post_upload_hook_timeout": 30,
    },
    # these are used for DB links on ALPHARATIO
    "IMAGES": {
        "imdb_75": "https://i.imgur.com/Mux5ObG.png",
        "tmdb_75": "https://i.imgur.com/r3QzUbk.png",
        "tvdb_75": "https://i.imgur.com/UWtUme4.png",
        "tvmaze_75": "https://i.imgur.com/ZHEF5nE.png",
        "mal_75": "https://i.imgur.com/PBfdP3M.png",
    },
    "TRACKERS": {
        # Which trackers do you want to upload to?
        # Note: Description layout settings (like screenshot grids, logos, etc.) can be overridden per-tracker.
        # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/description-builder.md
        # Available tracker: 1PTBA, AURA4K, ASIANCINEMA, AITHER, ANTHELION, ALPHARATIO, AMIGOSSHARE, AVISTAZ, BEYONDHD, BITHDTV, BITPORN, BJSHARE, BLUTOPIA, BRASILTRACKER, CAPYBARABR, CURUPIRA, SUIO, CINEMAZ, DIGITALCORE, DRUNKENSLUG, DARKPEERS, DESITORRENTS, EMUWAREZ, FUNFILE, FILELIST,
        # GREATPOSTERWALL, HDBITS, HDSPACE, HDTORRENTS, HOMIEHELPDESK, HAWKEUNO, INFINITYHD, IMMORTALSEED, ITATORRENTS, LAJIDUI, LEMONHD, LOCADORA, LASTDIGITALUNDERGROUND, LONGPT, LST, LATTEAM, LUMINARR, MIDNIGHTSCENE, MTEAM, NEBULANCE, ONLYENCODES,
        # NORDICQUALITY, NZBGEEK, OLDTOONSWORLD, PRIVATEHD, PORTUGAS, PTCAFE, PTERCLUB, PTFANS, PTGTK, PTZONE, PASSTHEPOPCORN, PEERGARDEN, PTSKIT, POLISHTORRENT, RACING4EVERYONE, RASTASTUGAN, REELFLIX, RAILGUNPT, RETROFLIX, RETROMOVIESCLUB, SAMARITANO, SHAREISLAND, SWARMAZON, SEEDPOOL, SPEEDAPP, SKIPTHECOMMERCIALS, TORRENTHR,
        # CINEMATIK, MAKINGOFF, ORPHEUS, TORRENTLEECH, THELEACHZONE, THEOLDSCHOOL, TOTHEGLORY, TORRENTEROS, TVCHAOSUK, ULCX, UNWALLED, UTOPIA, XINGYUNGEPT, YUSCENE, ZENITH
        # Only add the trackers you want to upload to on a regular basis
        "default_trackers": "",
        "1PTBA": {
            # Alias used to select this tracker with -tk/--trackers; case-insensitive.
            "cli_alias": "1PT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://1ptba.com/ to data/cookies/1PTBA.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "mediainfo_header": "",
            "audio_spectrogram_header": "",
            "custom_signature": "",
            "user_description": "",
            "custom_header": "",
            "custom_footer": "",
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "AITHER": {
            "cli_alias": "ATH",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Double upload duration in days
            "double_upload_until": 0,
            # For authorized users only. Do not change this unless you know what you are doing
            # Freeleech duration in days
            "freeleech_until": 0,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as refundable
            "refundable": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "ALPHARATIO": {
            "cli_alias": "AR",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # anon is not an option when uploading you need to change your privacy settings.
            "username": "",
            "password": "",
            "announce_url": "",
            "inject_delay": 0,
        },
        "AMIGOSSHARE": {
            "cli_alias": "ASC",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://amigos-share.club to data/cookies/AMIGOSSHARE.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            # The torrent is downloaded from the site after the upload; the announce URL (with your passkey) is
            # used for the private torrent that is sent, and is seeded if that download fails.
            "announce_url": "",
            # Set this to True if you want to allow external subtitles to be included in the upload
            "allow_ext_subtitles": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "ANTHELION": {
            "cli_alias": "ANT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "api_key": "",
            "announce_url": "",
            "anon": True,
            # Number of upload retry attempts for network/server errors (e.g. 500, timeouts).
            "max_retries": 5,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "ASIANCINEMA": {
            "cli_alias": "ACM",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "AURA4K": {
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to AURA4K modq for staff approval
            "modq": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "AVISTAZ": {
            "cli_alias": "AZ",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://avistaz.to to data/cookies/AVISTAZ.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # If True, the script performs a basic rules compliance check (e.g., codecs, region).
            # This does not cover all tracker rules. Set to False to disable.
            "check_for_rules": True,
            # AvistaZ does not allow frame overlays; use cached non-overlay images or recapture them.
            "image_tag_blacklist": ["overlay"],
            # The configurations below override the DEFAULT configuration
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "custom_description_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_audio_spectrogram": True,
            "add_dynamic_hdr_plot": True,
            "inject_delay": 0,
        },
        "BEYONDHD": {
            "cli_alias": "BHD",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "bhd_rss_key": "",
            "announce_url": "",
            # Send uploads to BeyondHD drafts
            "draft_default": False,
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "BITHDTV": {
            "cli_alias": "BHDTV",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # found under https://www.bit-hdtv.com/my.php
            "api_key": "",
            "announce_url": "https://trackerr.bit-hdtv.com/announce",
            # passkey found under https://www.bit-hdtv.com/my.php
            "my_announce_url": "https://trackerr.bit-hdtv.com/passkey/announce",
            "anon": True,
            "inject_delay": 0,
        },
        "BITPORN": {
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "api_key": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "BJSHARE": {
            "cli_alias": "BJS",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://bj-share.info to data/cookies/BJSHARE.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            # Two-factor authentication (2FA) must be enabled in your profile settings; otherwise, your session cookies will expire fairly quickly.
            "announce_url": "",
            # "use_for_search": False, set to True to import IDs and descriptions from BJ-Share torrents found in your client's torrent comments
            "use_for_search": False,
            "anon": True,
            # Set to False if during an anonymous upload you want your release group to be hidden
            "show_group_if_anon": True,
            # Set this to True if you want to allow external subtitles to be included in the upload
            "allow_ext_subtitles": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[align=center]As capturas de tela foram adaptadas para visualização em SDR, apenas para referência.[/align]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "disc_menu_header": "[size=3][b][align=center]Capturas de Tela do Menu do Disco[/align][/b][/size]",
            "audio_spectrogram_header": "[size=3][b][align=center]Espectrogramas de Áudio[/align][/b][/size]",
            "dynamic_hdr_plot_header": "[size=3][b][align=center]Metadados HDR Dinâmicos[/align][/b][/size]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "BLUTOPIA": {
            "cli_alias": "BLU",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "BRASILTRACKER": {
            "cli_alias": "BT",
            "link_dir_name": "",
            # Cookies required (export from https://brasiltracker.org/ to data/cookies/BRASILTRACKER.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # Set this to True if you want to allow external subtitles to be included in the upload
            "allow_ext_subtitles": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[align=center]As capturas de tela foram adaptadas para visualização em SDR, apenas para referência.[/align]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "inject_delay": 0,
        },
        "BROADCASTHENET": {
            "cli_alias": "BTN",
            # BTN accepts TV only. An API key is required for dupe searching and
            # resolving uploads that return a group page. Upload authentication
            # uses browser-exported cookies in data/cookies/BROADCASTHENET.txt.
            "link_dir_name": "",
            "use_for_search": False,
            "api_key": "",
            # Optional. When set, replaces the announce URL in the torrent downloaded from BTN
            # after upload (e.g. https://landof.tv/<passkey>/announce). Leave empty to keep BTN's.
            "announce_url": "",
            # Optional override for BTN's JSON-RPC endpoint.
            "api_url": "https://api.broadcasthe.net/",
            "inject_delay": 0,
        },
        "CAPYBARABR": {
            "cli_alias": "CBR",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # BiOMA Zipline API key/token for rehosting screenshots when uploading releases with tag 'BiOMA' (Host: https://img.thebioma.space/)
            "bioma_api_key": "",
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # Set this to True if you want to allow external subtitles to be included in the upload
            "allow_ext_subtitles": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]As capturas de tela foram adaptadas para visualização em SDR, apenas para referência.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Capturas de Tela[/h2]",
            "disc_menu_header": "[h2]Capturas de Tela do Menu do Disco[/h2]",
            "audio_spectrogram_header": "[h2]Espectrogramas de Áudio[/h2]",
            "dynamic_hdr_plot_header": "[h2]Metadados HDR Dinâmicos[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "CATHODERAYTUBE": {
            "cli_alias": "CRT",
            # Cookies required (export from https://www.cathode-ray.tube/ to data/cookies/CATHODERAYTUBE.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
        },
        "CINEMATIK": {
            "cli_alias": "TIK",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "CINEMAZ": {
            "cli_alias": "CZ",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://cinemaz.to to data/cookies/CINEMAZ.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # If True, the script performs a basic rules compliance check (e.g., codecs, region).
            # This does not cover all tracker rules. Set to False to disable.
            "check_for_rules": True,
            # CinemaZ does not allow frame overlays.
            "image_tag_blacklist": ["overlay"],
            # The configurations below override the DEFAULT configuration
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "custom_description_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_audio_spectrogram": True,
            "add_dynamic_hdr_plot": True,
            "inject_delay": 0,
        },
        "CURUPIRA": {
            "cli_alias": "CRP",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Your API Key, obtained from Perfil -> API Key
            "api_key": "",
            "anon": True,
            # Only block uploads when the existing release exactly matches files.
            "exact_match_only": False,
            "inject_delay": 0,
        },
        "CZTEAM": {
            "cli_alias": "CZT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Your site username and passkey, shown at https://czteam.me/my.php
            "username": "",
            "passkey": "",
        },
        "DARKPEERS": {
            "cli_alias": "DP",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            # Audio spectrograms will only be added for music uploads, as requested by the tracker staff.
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "DESITORRENTS": {
            "cli_alias": "DT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "DIGITALCORE": {
            "cli_alias": "DC",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # You can find your api key at Settings -> Security -> API Key -> Generate API Key
            "api_key": "",
            "anon": True,
            # Force DigitalCore uploads to re-host screenshots on img.digitalcore.club using DEFAULT.sharex_api_key.
            "force_rehost_images": False,
            # If True, the script will use the metadata-based title instead of the directory/file name.
            "use_metadata_name": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "DIRTYBYTES": {
            "cli_alias": "DB",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Generate your API key at Settings -> API key
            "api_key": "",
            "anon": True,
        },
        "DREADVAULT": {
            "cli_alias": "DVL",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Only block uploads when the existing release exactly matches files and size.
            "exact_match_only": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "DRUNKENSLUG": {
            "cli_alias": "DS",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Provide the DRUNKENSLUG API key here, can be found at https://drunkenslug.com/profile
            "api_key": "",
            # Maximum number of API hits the script may make within 24 hours for duplicate search.
            # Set to 0 to disable duplicate search via API.
            "daily_api_hit_limit": 0,
            # Only block uploads when the existing release exactly matches files.
            "exact_match_only": False,
            "inject_delay": 0,
        },
        "EMUWAREZ": {
            "cli_alias": "EMUW",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Use Spanish title instead of English title, if available
            "use_spanish_title": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "EXOTICAZ": {
            "cli_alias": "EXZ",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://exoticaz.to to data/cookies/EXOTICAZ.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            # Uploads are published with the torrent file downloaded from the site, which carries your passkey.
            "anon": True,
            # If True, the script performs a basic rules compliance check (e.g., file types, size, video sheets).
            # This does not cover all tracker rules. Set to False to disable.
            "check_for_rules": True,
            # ExoticaZ requires a video sheet per video and, for single clips, a full size screenshot.
            # Set DEFAULT "xxx_single_file_screens" to at least 1 when uploading single clips.
            # The configurations below override the DEFAULT configuration
            "custom_description_header": "",
            "custom_signature": "",
            "inject_delay": 0,
        },
        "FILEIPLAY": {
            "cli_alias": "FIP",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Personal token from https://fileiplay.org/settings/api with the torrents:upload and torrents:read scopes
            "api_key": "",
            # Integration API key from https://fileiplay.org/integrations (Show API key), used for duplicate searches
            # and to download the published .torrent, which carries your passkey.
            "torznab_api_key": "",
            "anon": True,
            # The TMDB logo is left out of FileIPlay descriptions unless this is set to True here.
            "add_logo": False,
        },
        "FILELIST": {
            "cli_alias": "FL",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "username": "",
            "passkey": "",
            "uploader_name": "https://filelist.io/Custom_Announce_URL",
            "anon": True,
            "inject_delay": 0,
        },
        "FLOOD": {
            "cli_alias": "FLD",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "api_key": "",
            "announce_url": "https://flood.st/announce/Custom_Announce_URL",
            "anon": True,
        },
        "FUNFILE": {
            "cli_alias": "FF",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "username": "",
            "password": "",
            # You can find your announce URL by downloading any torrent from FUNFILE, adding it to your client, and then copying the URL from the 'Trackers' tab.
            "announce_url": "",
            # Set to True if you want to check whether your upload fulfills corresponding requests. This may slightly slow down the upload process.
            "check_requests": False,
            # Set to True if you want to include the full MediaInfo in your upload description or False to include only the most relevant parts.
            "full_mediainfo": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "GREATPOSTERWALL": {
            "cli_alias": "GPW",
            "link_dir_name": "",
            # You can find your API key in Profile Settings -> Access Settings -> API Key. If there is no API, click "Reset your api key" and Save Profile.
            "api_key": "",
            # Optionally, export cookies from https://greatposterwall.com to data/cookies/GREATPOSTERWALL.txt to improve duplicate searches.
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            # You can find your announce URL at https://greatposterwall.com/upload.php
            "announce_url": "",
            # Upload with Exclusive flag
            "exclusive": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "HAPPYFAPPY": {
            "cli_alias": "HF",
            # Cookies required (export from https://www.happyfappy.net/ to data/cookies/HAPPYFAPPY.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            # Your personal announce URL is shown at https://www.happyfappy.net/upload.php
            # Images must be on pixhost, imgbox or imagebam; set one of them as an img_host_N.
            "announce_url": "",
            "anon": True,
        },
        "HAWKEUNO": {
            "cli_alias": "HUNO",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "use_for_search": False,
            "api_key": "",
            # You can find your announce URL at https://hawke.uno/upload
            "announce_url": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "HDBITS": {
            "cli_alias": "HDB",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            # for HDBITS you **MUST** have been granted uploading approval via Offers, you've been warned
            # Cookies required (export from https://hdbits.org/ to data/cookies/HDBITS.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "username": "",
            "passkey": "",
            "announce_url": "",
            "img_rehost": True,
            "inject_delay": 0,
        },
        "HDSPACE": {
            "cli_alias": "HDS",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://hd-space.org/ to data/cookies/HDSPACE.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # Set to True if you want to include the full MediaInfo in your upload description or False to include only the most relevant parts.
            "full_mediainfo": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "HDTORRENTS": {
            "cli_alias": "HDT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export to data/cookies/HDTORRENTS.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            # You can change the URL if the main site is down or if you encounter upload issues.
            # Keep in mind that changing the URL requires exporting the cookies again from the new domain.
            # Alternative domains:
            #   - https://hd-torrents.org/
            #   - https://hd-torrents.net/
            #   - https://hd-torrents.me/
            #   - https://hdts.ru/
            "url": "https://hd-torrents.me/",
            "anon": True,
            "announce_url": "",
            # Set to True if you want to include the full MediaInfo in your upload description or False to include only the most relevant parts.
            "full_mediainfo": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "HOMIEHELPDESK": {
            "cli_alias": "HHD",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "IMMORTALSEED": {
            "cli_alias": "IS",
            # Cookies required (export from https://immortalseed.me/ to data/cookies/IMMORTALSEED.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "announce_url": "",
            "anon": True,
            # Set to True if you want to include the full MediaInfo in your upload description or False to include only the most relevant parts.
            "full_mediainfo": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "INFINITYHD": {
            "cli_alias": "IHD",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "IPTORRENTS": {
            "cli_alias": "IPT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export to data/cookies/IPTORRENTS.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            # By default, IPTORRENTS removes all dots from the upload name, causing audio codecs to be named incorrectly, for example, "DTS 5 1" instead of "DTS 5.1".
            # It also does not have the option to set the IMDb during upload.
            # Set this to True to edit the torrent after the upload to force the correct naming and IMDb.
            "force_data": False,
            # Set to True if you want to include the full MediaInfo in your upload description or False to include only the most relevant parts.
            "full_mediainfo": False,
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "ITATORRENTS": {
            "cli_alias": "ITT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "1PTBA": {
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://1ptba.com/ to data/cookies/1PTBA.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "mediainfo_header": "",
            "audio_spectrogram_header": "",
            "custom_signature": "",
            "user_description": "",
            "custom_header": "",
            "custom_footer": "",
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "LAJIDUI": {
            "cli_alias": "LAJIDUI",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://pt.lajidui.top/ to data/cookies/LAJIDUI.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "LASTDIGITALUNDERGROUND": {
            "cli_alias": "LDU",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "LATTEAM": {
            "cli_alias": "LT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "LEMONHD": {
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://lemonhd.net/ to data/cookies/LEMONHD.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "mediainfo_header": "",
            "audio_spectrogram_header": "",
            "custom_signature": "",
            "user_description": "",
            "custom_header": "",
            "custom_footer": "",
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "LOCADORA": {
            "cli_alias": "LCD",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Set this to True if you want to allow external subtitles to be included in the upload
            "allow_ext_subtitles": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]As capturas de tela foram adaptadas para visualização em SDR, apenas para referência.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Capturas de Tela[/h2]",
            "disc_menu_header": "[h2]Capturas de Tela do Menu do Disco[/h2]",
            "audio_spectrogram_header": "[h2]Espectrogramas de Áudio[/h2]",
            "dynamic_hdr_plot_header": "[h2]Metadados HDR Dinâmicos[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "LONGPT": {
            "cli_alias": "LPT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://longpt.org/ to data/cookies/LONGPT.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "LST": {
            "cli_alias": "LST",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # Send uploads to LST drafts
            "draft": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "LUMINARR": {
            "cli_alias": "LUME",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "MAKINGOFF": {
            "cli_alias": "MKO",
            # Cookies required (export from https://www.makingoff.org/ to data/cookies/MAKINGOFF.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # trackers to be added on the torrents
            # one per line
            "trackers": """
                """,
            # Set this to True if you want to allow external subtitles to be included in the upload
            "allow_ext_subtitles": True,
        },
        "MIDNIGHTSCENE": {
            "cli_alias": "MS",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "MTEAM": {
            "cli_alias": "MTEAM",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "api_key": "",
            "anon": True,
            "base_url": "kp.m-team.cc",
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "NEBULANCE": {
            "cli_alias": "NBL",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "api_key": "",
            "announce_url": "",
            "inject_delay": 0,
        },
        "NORDICQUALITY": {
            "cli_alias": "NQ",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "NZBGEEK": {
            "api_key": "",
            # Maximum number of API hits the script may make within 24 hours for duplicate search.
            # Set to 0 to disable duplicate search via API.
            "daily_api_hit_limit": 0,
            "inject_delay": 0,
        },
        "OLDTOONSWORLD": {
            "cli_alias": "OTW",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "ONLYENCODES": {
            "cli_alias": "OE",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            # OnlyEncodes forbids description logos and requires linked medium screenshots
            # from the first episode/movie rather than screenshots for every pack item.
            "add_logo": False,
            "logo_size": "",
            "thumbnail_size": 350,
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": 0,
            "pack_thumb_size": 350,
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "ORPHEUS": {
            "cli_alias": "OPS",
            # Orpheus Gazelle API token. Do not commit a real token.
            "api_key": "",
            # Obtain from https://orpheus.network/upload.php
            "announce_url": "",
            "inject_delay": 0,
        },
        "PTZONE": {
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://ptzone.xyz/ to data/cookies/PTZONE.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "mediainfo_header": "",
            "audio_spectrogram_header": "",
            "custom_signature": "",
            "user_description": "",
            "custom_header": "",
            "custom_footer": "",
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "PASSTHEPOPCORN": {
            "cli_alias": "PTP",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "add_web_source_to_desc": True,
            "ApiUser": "ptp api user",
            "api_key": "",
            "username": "",
            "password": "",
            "announce_url": "",
            "inject_delay": 5,
        },
        "PEERGARDEN": {
            "cli_alias": "PG",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Only block uploads when the existing release exactly matches files and size.
            "exact_match_only": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "POLISHTORRENT": {
            "cli_alias": "PTT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "PORTUGAS": {
            "cli_alias": "PT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "PRIVATEHD": {
            "cli_alias": "PHD",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://privatehd.to/ to data/cookies/PRIVATEHD.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # If True, the script performs a basic rules compliance check (e.g., codecs, region).
            # This does not cover all tracker rules. Set to False to disable.
            "check_for_rules": True,
            # PrivateHD does not allow frame overlays.
            "image_tag_blacklist": ["overlay"],
            # The configurations below override the DEFAULT configuration
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "custom_description_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_audio_spectrogram": True,
            "add_dynamic_hdr_plot": True,
            "inject_delay": 0,
        },
        "PTCAFE": {
            "cli_alias": "PTCAFE",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://ptcafe.club/ to data/cookies/PTCAFE.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        # PTERCLUB support is currently experimental and may not work reliably.
        "PTERCLUB": {
            "cli_alias": "PTER",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "passkey": "passkey",
            "img_rehost": False,
            "username": "",
            "password": "",
            "ptgen_api": "",
            "anon": True,
            "inject_delay": 0,
        },
        "PTFANS": {
            "cli_alias": "PTFANS",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://ptfans.cc/ to data/cookies/PTFANS.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "PTGTK": {
            "cli_alias": "PTGTK",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://pt.gtkpw.xyz to data/cookies/PTGTK.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "PTSKIT": {
            "cli_alias": "PTS",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://www.ptskit.org to data/cookies/PTSKIT.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "PTZONE": {
            "cli_alias": "PTZ",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://ptzone.xyz/ to data/cookies/PTZONE.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "mediainfo_header": "",
            "audio_spectrogram_header": "",
            "custom_signature": "",
            "user_description": "",
            "custom_header": "",
            "custom_footer": "",
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "QINGWA": {
            "cli_alias": "QW",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://www.qingwapt.com/ to data/cookies/QINGWA.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "mediainfo_header": "",
            "audio_spectrogram_header": "",
            "custom_signature": "",
            "user_description": "",
            "custom_header": "",
            "custom_footer": "",
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "RACING4EVERYONE": {
            "cli_alias": "R4E",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "announce_url": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "RAILGUNPT": {
            "cli_alias": "RPT",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://bilibili.download to data/cookies/RAILGUNPT.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "RASTASTUGAN": {
            "cli_alias": "RAS",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "REELFLIX": {
            "cli_alias": "RF",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "RETROFLIX": {
            "cli_alias": "RTF",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "username": "",
            "password": "",
            # get_it_by_running_/api/ login command from https://retroflix.club/api/doc
            "api_key": "",
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "RETROMOVIESCLUB": {
            "cli_alias": "RMC",
            # Instead of using the tracker name for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if you want to use Retro Movies Club for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "ROCKETHD": {
            "cli_alias": "RHD",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if you want to use RocketHD for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Use German title instead of English title, if available
            "use_german_title": False,
        },
        "SAMARITANO": {
            "cli_alias": "SAM",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            # API key for the tracker-only image host at https://img.samaritano.cc/
            "image_host_api_key": "",
            # Rehost this tracker's images on img.samaritano.cc. Failures keep the default image host.
            "force_rehost_images": False,
            "anon": True,
            # Set this to True if you want to allow external subtitles to be included in the upload
            "allow_ext_subtitles": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]As capturas de tela foram adaptadas para visualização em SDR, apenas para referência.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Capturas de Tela[/h2]",
            "disc_menu_header": "[h2]Capturas de Tela do Menu do Disco[/h2]",
            "audio_spectrogram_header": "[h2]Espectrogramas de Áudio[/h2]",
            "dynamic_hdr_plot_header": "[h2]Metadados HDR Dinâmicos[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "SEEDPOOL": {
            "cli_alias": "SP",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Only block uploads when the existing release exactly matches files and size.
            "exact_match_only": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "SHAREISLAND": {
            "cli_alias": "SHRI",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Use Italian title instead of English title, if available
            "use_italian_title": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "SIMURG": {
            "cli_alias": "SMG",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Simurg API token: Settings > API > API Keys. Do not commit a real token.
            "api_key": "",
            # Obtain from https://simurg.world/upload.php
            "announce_url": "",
        },
        "SKIPTHECOMMERCIALS": {
            "cli_alias": "STC",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "SPEEDAPP": {
            "cli_alias": "SPD",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # You can create an API key here https://speedapp.io/profile/api-tokens. Required Permission: Upload torrents
            "api_key": "",
            # Select the upload channel, if you don't know what this is, leave it empty.
            # You can also set this manually using the args -ch or --channel, without '@'. Example: @spd -> '-ch spd'.
            "channel": "",
            # If True, the script will use the metadata-based title instead of the folder/file name.
            "use_metadata_name": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "SUIO": {
            "cli_alias": "SUIO",
            # Secret Usenet Indexer: o[redacted]nzbs
            # Please do not share, discuss or mention this indexer's real name in issues or pull requests; nor should you ask what it is.
            # Paste the indexer's base url below (e.g., https://indexer.com)
            # Upload will only proceed if the domain matches the allowed one, protecting your credentials.
            "base_url": "",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Your Username
            "username": "",
            # Your API Key
            "api_key": "",
            # Maximum number of API hits the script may make within 24 hours for duplicate search.
            # Set to 0 to disable duplicate search via API.
            "daily_api_hit_limit": 0,
            "anon": True,
            # Only block uploads when the existing release exactly matches files.
            "exact_match_only": False,
            # If False, the indexer will decide (uses ID "0").
            # If True, the script will resolve the audio language ID:
            #   - 1 language: uses the ID for that language.
            #   - 2 languages: uses the ID for the language different from the original language.
            #   - 3 or more languages: uses the ID for "multi" ("9").
            #   - No languages: uses "0" (Auto).
            "resolve_language": True,
            "inject_delay": 0,
        },
        "SWARMAZON": {
            "cli_alias": "SN",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "api_key": "",
            "announce_url": "",
            "inject_delay": 0,
        },
        "THELEACHZONE": {
            "cli_alias": "TLZ",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "THEOLDSCHOOL": {
            "cli_alias": "TOS",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Mon profil > Réglages > Clé API
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            # Mon profil > Réglages > Passkey
            "announce_url": "",
            "anon": True,
            # Upload with Exclusive flag (team of staff only)
            "exclusive": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "TORRENTEROS": {
            "cli_alias": "TTR",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "TORRENTHAVEN": {
            "cli_alias": "THN",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "TORRENTLEECH": {
            "cli_alias": "TL",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Set to False if you don't have access to the API (e.g., if you're a trial uploader). Note: this may not work sometimes due to Cloudflare restrictions.
            # If you are not going to use the API, you will need to export cookies from https://www.torrentleech.org/ to data/cookies/TORRENTLEECH.txt.
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "api_upload": True,
            # You can find your passkey at your profile (https://www.torrentleech.org/profile/[YourUserName]/view) -> Torrent Passkey
            "passkey": "",
            "anon": True,
            # Rehost images to the TORRENTLEECH image host. Does not work with the API upload method.
            # Keep in mind that screenshots are only anonymous if you enable the "Anonymous Gallery Uploads" option in your profile settings.
            "img_rehost": True,
            # Set to True if you want to include the full MediaInfo in your upload description or False to include only the most relevant parts.
            "full_mediainfo": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "audio_spectrogram_header": "",
            "dynamic_hdr_plot_header": "",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "TOTHEGLORY": {
            "cli_alias": "TTG",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            "username": "",
            "password": "",
            "login_question": "",
            "login_answer": "",
            "user_id": "",
            "announce_url": "",
            "anon": True,
            "inject_delay": 0,
        },
        "TVCHAOSUK": {
            "cli_alias": "TVC",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # 2 is listed as max images in rules. Please do not change unless you have permission
            "image_count": 2,
            "api_key": "",
            "announce_url": "",
            "anon": True,
            "inject_delay": 0,
        },
        "ULCX": {
            "cli_alias": "ULCX",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "UNWALLED": {
            "link_dir_name": "",
            "use_for_search": False,
            "api_key": "",
            "announce_url": "",
            "anon": True,
            "category": "",
            "type": "",
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "custom_signature": "",
            "add_bluray_link": False,
            "use_bluray_images": False,
            "add_audio_spectrogram": False,
            "inject_delay": 0,
        },
        "UTOPIA": {
            "cli_alias": "UTP",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "XINGYUNGEPT": {
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # Cookies required (export from https://pt.xingyungept.org/ to data/cookies/XINGYUNGEPT.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "",
            "disc_menu_header": "",
            "mediainfo_header": "",
            "audio_spectrogram_header": "",
            "custom_signature": "",
            "user_description": "",
            "custom_header": "",
            "custom_footer": "",
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "YUSCENE": {
            "cli_alias": "YUS",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "ZENITH": {
            "cli_alias": "ZNTH",
            # Instead of using the tracker acronym for folder name when sym/hard linking, you can use a custom name
            "link_dir_name": "",
            # "use_for_search": False, set to True if using this tracker for automatic ID searching or description parsing
            "use_for_search": False,
            "api_key": "",
            "anon": True,
            # Send uploads to the moderation queue for staff review and approval
            "modq": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as featured
            "featured": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload with double upload credit
            "doubleup": False,
            # For authorized users only. Do not change this unless you know what you are doing
            # Upload as sticky/pinned
            "sticky": False,
            # The configurations below override the DEFAULT configuration
            "add_logo": True,
            "logo_size": "",
            "thumbnail_size": "",
            "screens_per_row": "",
            "episode_overview": True,
            "tonemapped_header": "[note]Screenshots have been adapted for SDR viewing, for reference only.[/note]",
            # Ignored: Zenith does not allow extra screenshots for each episode or disc in packs.
            "multiScreens": "",
            "pack_thumb_size": "",
            "charLimit": "",
            "fileLimit": "",
            "processLimit": "",
            "custom_description_header": "",
            "screenshot_header": "[h2]Screenshots[/h2]",
            "disc_menu_header": "[h2]Disc Menu Screenshots[/h2]",
            "audio_spectrogram_header": "[h2]Audio Spectrogram[/h2]",
            "dynamic_hdr_plot_header": "[h2]Dynamic HDR Metadata[/h2]",
            "custom_signature": "",
            "add_bluray_link": True,
            "use_bluray_images": True,
            "bluray_image_size": "",
            "add_audio_spectrogram": True,
            "inject_delay": 0,
        },
        "ZMPT": {
            "cli_alias": "ZMPT",
            # Cookies required (export from https://zmpt.cc/ to data/cookies/ZMPT.txt).
            # See: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/example-config.md#how-to-export-cookies
            "announce_url": "",
            "anon": True,
        },
        "MANUAL": {
            "cli_alias": "MANUAL",
            # Replace link with filebrowser (https://github.com/filebrowser/filebrowser) link to the Upload-Assistant directory, this will link to your filebrowser instead of uploading to uguu.se
            "filebrowser": "",
        },
    },
    # enable_search to True will automatically try and find a suitable hash to save having to rehash when creating torrents
    # If you find issue, especially in local/remote path mapping, use the "--debug" argument to print out some related details
    "TORRENT_CLIENTS": {
        # Name your torrent clients here, for example, this example is named "qbittorrent" and is set as default_torrent_client above
        # All options target the torrent client API/Web UI; secure that service if it has WAN access
        # **DO NOT** modify torrent_client name, eg: "qbit"
        # See https://github.com/wastaken7/Upload-Assistant/blob/development/docs/configuration.md#torrent-clients
        "qbittorrent": {
            "torrent_client": "qbit",
            # qui reverse proxy: https://getqui.com/docs/features/reverse-proxy
            # Create a Client Proxy API Key in qui (Settings → Client Proxy Keys), pick the instance, paste the full proxy URL here.
            # Example: "http://localhost:7476/proxy/<your-client-api-key>".
            # qui is not used for bandwidth measurements; see docs/upload-order-and-bandwidth-control.md.
            "qui_proxy_url": "",
            # enable_search to True will automatically try and find a suitable hash to save having to rehash when creating torrents
            "enable_search": True,
            "qbit_url": "http://127.0.0.1",
            "qbit_port": "8080",
            "qbit_user": "",
            "qbit_pass": "",
            # API Key authentication (stateless, qBittorrent v5.2.0+). When set, qbit_user and qbit_pass are ignored.
            "qbit_api_key": "",
            # Optional qBittorrent BT_backup directory. Used together with qui/API search;
            # reading .torrent files locally can substantially speed up candidate validation.
            # Use double-backslashes on Windows, e.g. "C:\\Users\\<YOUR_USER>\\AppData\\Local\\qBittorrent\\BT_backup".
            # WARNING: this should not be used when using SQLite Mode for qBittorrent.
            "torrent_storage_dir": "",
            # List of trackers to activate "super-seed" (or "initial seeding") mode when adding the torrent.
            # https://www.bittorrent.org/beps/bep_0016.html
            # Super-seed mode is NOT recommended for general use.
            # Super-seed mode is only recommended for initial seeding servers where bandwidth management is paramount.
            "super_seed_trackers": [""],
            # Use the UA tracker acronym as a tag in qBitTorrent
            "use_tracker_as_tag": False,
            "qbit_tag": "",
            "qbit_cat": "",
            # If using cross-seeding, add its qBittorrent tag/category here.
            "qbit_cross_tag": "",
            "qbit_cross_cat": "",
            # qBittorrent content layout for all torrents added through this client.
            # Use "Original", "Subfolder", or "NoSubfolder".
            "content_layout": "Original",
            # Choose symbolic links, hard links, or an empty value to use the original path.
            # This will disable any automatic torrent management if set.
            # use either "symlink" or "hardlink"
            # On Windows, symbolic links may require administrator privileges. Both link types
            # require an NTFS/ReFS filesystem, and hard links must remain on the same drive.
            "linking": "",
            # Allow fallback to inject torrent into qBitTorrent using the original path
            # when linking error. eg: unsupported file system.
            "allow_fallback": True,
            # A folder or list of folders that will contain the linked content
            # if using hardlinking, the linked folder must be on the same drive/volume as the original content,
            # with UA mapping the correct location if multiple paths are specified.
            # Use local paths, remote path mapping will be handled.
            # only single \ on windows, path will be handled by UA
            "linked_folder": [""],
            # Remote path mapping (docker/etc.) CASE SENSITIVE
            "local_path": [""],
            "remote_path": [""],
            # For a client that seeds its own copy of your files, such as a seedbox.
            # Before adding, UA looks in this client for a complete torrent holding the same files (names and sizes)
            # and seeds the new torrent from that torrent's folder, without path mapping or linking.
            # "prefer": use that copy when found, otherwise use the path mapping above.
            # "require": add the torrent to this client only when such a copy is found.
            # Leave empty to always use the path mapping.
            # Guide: https://github.com/wastaken7/Upload-Assistant/blob/development/docs/seeding-from-a-second-client.md
            "match_existing_content": "",
            # Set to False to skip verify certificate for HTTPS connections; for instance, if the connection is using a self-signed certificate.
            # "VERIFY_WEBUI_CERTIFICATE": True,
        },
        "qbittorrent_searching": {
            # an example of using a qBitTorrent client just for searching, when using another client for injection
            "torrent_client": "qbit",
            # qui reverse proxy: https://getqui.com/docs/features/reverse-proxy
            # Create a Client Proxy API Key in qui (Settings → Client Proxy Keys), pick the instance, paste the full proxy URL here.
            # Example: "http://localhost:7476/proxy/<your-client-api-key>".
            # qui is not used for bandwidth measurements; see docs/upload-order-and-bandwidth-control.md.
            "qui_proxy_url": "",
            # enable_search to True will automatically try and find a suitable hash to save having to rehash when creating torrents
            "enable_search": True,
            "qbit_url": "http://127.0.0.1",
            "qbit_port": "8080",
            "qbit_user": "",
            "qbit_pass": "",
        },
        "rtorrent": {
            "torrent_client": "rtorrent",
            "rtorrent_url": "https://user:password@server.host.tld:443/username/rutorrent/plugins/httprpc/action.php",
            # path/to/session folder
            "torrent_storage_dir": "",
            "rtorrent_label": "",
            # Choose symbolic links, hard links, or an empty value to use the original path.
            # this will disable any automatic torrent management if set
            # use either "symlink" or "hardlink"
            # On Windows, symbolic links may require administrator privileges. Both link types
            # require an NTFS/ReFS filesystem, and hard links must remain on the same drive.
            "linking": "",
            # Allow fallback to inject torrent into qBitTorrent using the original path
            # when linking error. eg: unsupported file system.
            "allow_fallback": True,
            # A folder or list of folders that will contain the linked content
            # if using hardlinking, the linked folder must be on the same drive/volume as the original content,
            # with UA mapping the correct location if multiple paths are specified.
            # Use local paths, remote path mapping will be handled.
            # only single \ on windows, path will be handled by UA
            "linked_folder": [""],
            # Remote path mapping (docker/etc.) CASE SENSITIVE
            "local_path": [""],
            "remote_path": [""],
        },
        "deluge": {
            "torrent_client": "deluge",
            "deluge_url": "localhost",
            "deluge_port": "8080",
            "deluge_user": "username",
            "deluge_pass": "password",
            # path/to/session folder
            "torrent_storage_dir": "",
            # Remote path mapping (docker/etc.) CASE SENSITIVE
            "local_path": [""],
            "remote_path": [""],
        },
        "transmission": {
            "torrent_client": "transmission",
            # http or https
            "transmission_protocol": "http",
            "transmission_username": "username",
            "transmission_password": "password",
            "transmission_host": "localhost",
            "transmission_port": 9091,
            "transmission_path": "/transmission/rpc",
            #  path/to/config/torrents folder
            "torrent_storage_dir": "",
            "transmission_label": "",
            # Remote path mapping (docker/etc.) CASE SENSITIVE
            "local_path": [""],
            "remote_path": [""],
        },
        "watch": {
            "torrent_client": "watch",
            # /Path/To/Watch/Folder
            "watch_folder": "",
        },
    },
    "USENET": {
        # --- GENERAL SETTINGS ---
        # Set to True to enable Usenet uploading
        "enabled": False,
        # --- SERVER CONNECTION ---
        # Usenet NNTP host, port, credentials
        "host": "",
        "port": "443",
        "username": "",
        "password": "",
        "ssl": True,
        "connections": "20",
        "newsgroups": "alt.binaries.boneless",
        # Custom poster name/email. E.g. "Uploader <upload@assistant.org>"
        "poster": "Uploader <upload@assistant.org>",
        # If True, poster will be randomized for anonymity/obfuscation
        "random_poster": True,
        # --- ARCHIVING AND PARITY ---
        # Set to True to skip 7z archiving entirely and post files directly.
        # Useful with pesto, which handles obfuscation and PAR2 natively.
        "skip_archive": False,
        # Volume size for 7z splitting (e.g. "100M", "50M", or "auto" for dynamic sizing)
        "rar_volume_size": "auto",
        # Password for 7z archive. Can be a fixed string or "random" to generate a unique random password.
        # Leave empty or set to None to disable archive encryption.
        "archive_password": "random",
        # Percentage of parity recovery blocks for PAR2 (e.g. "10")
        "par2_percentage": "10",
        # Obfuscate the subject line of the NNTP post to prevent DMCA takedowns
        "obscure_subject": True,
        # Pesto only: "light" also keeps archive filenames opaque inside the
        # NZB; "full" hides them on NNTP but restores readable names in the NZB.
        # Existing configs without this key retain Pesto's previous "full" mode.
        "pesto_obfuscation_mode": "light",
        # --- UPLOADER AND VERIFICATION ---
        # Uploader backend: "nyuu" (default) or "pesto"
        # pesto handles PAR2 and NZB password injection internally
        "usenet_uploader": "nyuu",
        # pesto only: for TV season packs, upload each top-level episode as an
        # independent release and also generate a consolidated season NZB.
        # Every generated NZB is then submitted to the configured indexers.
        "pesto_season_upload": False,
        # pesto only: a streaming STAT check that runs concurrently with the
        # upload, confirming each article shortly after it posts. Missing
        # articles are reposted and reverified automatically; the upload is
        # only considered successful (and the NZB kept) once every article is
        # confirmed. Strongly recommended — without this, a "successfully
        # posted" article can still be missing/unpropagated on the server,
        # producing a broken NZB.
        "pesto_check": True,
        # Seconds to wait after each article posts before its first STAT check
        # (pesto --check-delay).
        "pesto_check_delay": 5,
        # Number of STAT attempts per article before marking it missing (pesto --check-retries)
        "pesto_check_retries": 3,
        # Dedicated parallel connections for the check queue, carved out of
        # "connections" (never opened on top of it). Empty = pesto auto-derives
        # a small pool from "connections"; if "connections" is too low to spare
        # any, the check is skipped (pesto --check-connections)
        "pesto_check_connections": "",
        # Number of repost+reverify rounds to attempt for articles still missing
        # after the check (pesto --check-post-retries). Raise this on providers
        # with slower or less reliable propagation. Unlike the other
        # pesto_check_* options, "" here does not disable this — it defers to
        # pesto's own default of 1 round instead of 3.
        "pesto_check_post_retries": 3,
        # nyuu only: verify every article is retrievable on the server while
        # posting is still in progress. Missing articles are reposted and
        # reverified automatically; the upload is only considered successful
        # (and the NZB kept) once every article is confirmed. Strongly
        # recommended — without this, a "successfully posted" article can
        # still be missing/unpropagated on the server, producing a broken NZB.
        "nyuu_check": True,
        # Seconds to wait after each article posts before verifying that article
        # (nyuu --check-delay). Keep this low; a larger value can back up the
        # check queue on large uploads and stall posting.
        "nyuu_check_delay": 5,
        # Number of check attempts per article before marking it missing (nyuu --check-tries)
        "nyuu_check_retries": 3,
        # Connections used for the verification pass (nyuu --check-connections).
        # Empty (default) splits "connections" in half between posting and
        # checking, since nyuu checks concurrently with posting and throttles
        # posting speed to match if checking can't keep up — this keeps the
        # combined connection count within what you configured. Set explicitly
        # to instead post at the full "connections" count with this many
        # additional connections dedicated to checking.
        "nyuu_check_connections": "",
        # --- BINARY PATHS ---
        # Paths to binaries (defaults to looking in PATH, downloaded automatically if not found)
        # Available at: https://github.com/animetosho/nyuu
        "nyuu_path": "nyuu",
        # Available at: https://github.com/animetosho/par2cmdline-turbo
        "par2_path": "par2",
        # Available at: https://github.com/franzopl/pesto
        "pesto_path": "pesto",
        # Available at: https://www.7-zip.org/
        "7z_path": "7z",
        # --- OUTPUT PATHS ---
        # Where to output generated NZB files (if empty, saves to tmp directory)
        "nzb_output_dir": "",
        # Temporary directory for Usenet uploads where compressed volumes are stored (if empty, saves to tmp directory)
        "usenet_tmp_dir": "",
    },
}
