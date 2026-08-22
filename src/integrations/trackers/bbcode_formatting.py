# Upload Assistant © 2025 Audionut & wastaken7 — Licensed under UAPL v1.0
import html
import re
import urllib.parse
from pathlib import Path
from typing import Any

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger

# Bold - KEEP
# Italic - KEEP
# Underline - KEEP
# Strikethrough - KEEP
# Color - KEEP
# URL - KEEP
# PARSING - Probably not exist in uploads
# Spoiler - KEEP

# QUOTE - CONVERT to CODE
# PRE - CONVERT to CODE
# Hide - CONVERT to SPOILER
# COMPARISON - CONVERT

# LIST - REMOVE TAGS/REPLACE with * or something

# Size - REMOVE TAGS

# Align - REMOVE (ALL LEFT ALIGNED)
# VIDEO - REMOVE
# HR - REMOVE
# MEDIAINFO - REMOVE
# MOVIE - REMOVE
# PERSON - REMOVE
# USER - REMOVE
# IMG - REMOVE?
# INDENT - Probably not an issue, but maybe just remove tags


class BBCODE:
    def __init__(self) -> None:
        pass

    @staticmethod
    def _is_hdbits_url(url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        return host == "hdbits.org" or host.endswith(".hdbits.org")

    @staticmethod
    def _normalized_description(description: str) -> str:
        return html.unescape(description).replace("\r\n", "\n")

    @staticmethod
    def _remove_hdb_comparison_sections(desc: str) -> str:
        sections = re.finditer(
            r"\[center\]\s*\[b\].*?(Comparison|vs).*?\[\/b\][\s\S]*?\[\/center\]",
            desc,
            flags=re.IGNORECASE,
        )
        for section in sections:
            section_text = section.group(0)
            if re.search(r"hdbits\.org", section_text, flags=re.IGNORECASE):
                desc = desc.replace(section_text, "")
        lines = re.finditer(r"(.*comparison.*)\n", desc, flags=re.IGNORECASE)
        for match in lines:
            position = match.start()
            next_lines = desc[position : position + 500].split("\n", 3)[:3]
            next_text = "\n".join(next_lines)
            if re.search(r"hdbits\.org", next_text, flags=re.IGNORECASE):
                desc = desc.replace(
                    desc[position : position + len(next_text)], ""
                )
        return desc

    @staticmethod
    def _remove_hdb_private_content(desc: str) -> str:
        patterns = (
            r"\[url=https?:\/\/(img\.|t\.)?hdbits\.org[^\]]*\]\[\/url\]",
            r"\[url(?:=|\])https?:\/\/(?:img\.|t\.)?hdbits\.org[^\]]*\].*?\[\/url\]",
            r"\[img\][\s\S]*?(?:img\.|t\.)?hdbits\.org[\s\S]*?\[\/img\]",
            r"https?:\/\/(?:img\.|t\.)?hdbits\.org\/[^\s\[\]]+",
            r"\[url[^\]]*hdbits\.org[^\]]*\](.*?)\[\/url\]",
            r"\[url=https?:\/\/[^\]]*hdbits\.org[^\]]*\]\[\/url\]",
            r"\[center\]\s*\[b\].*?(Comparison|vs).*?\[\/b\][\s\S]*?\[\/center\]",
            r"\[center\]\s*\[\/center\]",
        )
        for pattern in patterns:
            desc = re.sub(pattern, "", desc, flags=re.IGNORECASE)
        return re.sub(r"\n{3,}", "\n\n", desc)

    @staticmethod
    def _hdb_image_entry(web_url: str, img_url: str) -> dict[str, Any]:
        raw_url = img_url
        host = (urllib.parse.urlparse(img_url).hostname or "").lower()
        if host == "thumbs2.imgbox.com":
            raw_url = img_url.replace(
                "thumbs2.imgbox.com", "images2.imgbox.com"
            ).replace("_t.png", "_o.png")
        return {"img_url": img_url, "raw_url": raw_url, "web_url": web_url}

    @classmethod
    def _extract_hdb_images(
        cls, desc: str
    ) -> tuple[str, list[dict[str, Any]]]:
        pattern = (
            r"\[url=(https?:\/\/[^\]]+)\]\[img\]"
            r"(https?:\/\/[^\]]+)\[\/img\]\[\/url\]"
        )
        matches: list[tuple[str, str]] = re.findall(
            pattern, desc, flags=re.IGNORECASE
        )
        images: list[dict[str, Any]] = []
        for web_url, img_url in matches:
            images.append(cls._hdb_image_entry(web_url, img_url))
            desc = desc.replace(
                f"[url={web_url}][img]{img_url}[/img][/url]", ""
            )
        return desc, images

    def clean_hdb_description(
        self, description: str
    ) -> tuple[str, list[dict[str, Any]]]:
        desc = self._normalized_description(description)
        desc = self._remove_hdb_comparison_sections(desc)
        desc = self._remove_hdb_private_content(desc)
        desc, imagelist = self._extract_hdb_images(desc)
        cleaned = desc.strip()
        if self.is_only_bbcode(cleaned):
            return "", imagelist
        return cleaned, imagelist

    @staticmethod
    def _save_bhd_framestor_nfo(meta: Meta, desc: str) -> None:
        if not ("framestor" in meta and meta.framestor):
            return
        save_path = Path(meta.base_dir) / "tmp" / meta.uuid
        save_path.mkdir(parents=True, exist_ok=True)
        nfo_file_path = save_path / "bhd.nfo"
        with nfo_file_path.open("w", encoding="utf-8") as file_handle:
            try:
                file_handle.write(desc)
            finally:
                file_handle.close()
        logger.info(f"[green]FraMeSToR NFO saved to {nfo_file_path}")
        meta.nfo = True
        meta.bhd_nfo = True

    @staticmethod
    def _clean_bhd_image_tags(desc: str) -> str:
        desc = re.sub(r"\[size=.*?\]", "", desc)
        desc = desc.replace("[/size]", "")
        desc = desc.replace("<", "/")
        desc = desc.replace("<", "\\")
        desc = re.sub(
            r"\[img(?:=[^\]]*)?\][\s\S]*?\[\/img\]",
            "",
            desc,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\[img=[^\]]*\]", "", desc, flags=re.IGNORECASE)

    @staticmethod
    def _extract_bhd_loose_images(
        desc: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        loose_images = re.findall(
            r"(https?:\/\/[^\s\[\]]+\.(?:png|jpg))",
            desc,
            flags=re.IGNORECASE,
        )
        images: list[dict[str, Any]] = []
        for img_url in dict.fromkeys(loose_images):
            images.append(
                {"img_url": img_url, "raw_url": img_url, "web_url": img_url}
            )
            desc = desc.replace(img_url, "")
        return desc, images

    @staticmethod
    def _remove_bhd_image_urls(desc: str, images: list[dict[str, Any]]) -> str:
        for image in images:
            img_url = re.escape(image["img_url"])
            desc = re.sub(
                rf"\[URL={img_url}\]\[/URL\]", "", desc, flags=re.IGNORECASE
            )
            desc = re.sub(
                rf"\[URL={img_url}\]\[img[^\]]*\]{img_url}\[/img\]\[/URL\]",
                "",
                desc,
                flags=re.IGNORECASE,
            )
        desc = re.sub(
            r"\[img\][\s\S]*?\[\/img\]", "", desc, flags=re.IGNORECASE
        )
        desc = re.sub(r"\[img=[\s\S]*?\]", "", desc, flags=re.IGNORECASE)
        return re.sub(
            r"\[URL=[\s\S]*?\]\[\/URL\]", "", desc, flags=re.IGNORECASE
        )

    @staticmethod
    def _bhd_flux_description(desc: str) -> str:
        desc = re.sub("\n\n+", "\n\n", desc.rstrip().strip("\n"))
        if not desc.replace("\n", "").strip():
            logger.info("[yellow]Description is empty after cleaning.")
            return ""
        return f"[code]{desc}[/code]"

    def clean_bhd_description(
        self, description: str, meta: Meta
    ) -> tuple[str, list[dict[str, Any]]]:
        desc = self._normalized_description(description)
        self._save_bhd_framestor_nfo(meta, desc)
        desc = self._clean_bhd_image_tags(desc)
        desc, imagelist = self._extract_bhd_loose_images(desc)
        desc = self._remove_bhd_image_urls(desc, imagelist)
        cleaned = self._bhd_flux_description(desc) if meta.flux else ""
        if self.is_only_bbcode(cleaned):
            return "", imagelist
        return cleaned, imagelist

    @staticmethod
    def _clean_ptp_private_links(desc: str) -> str:
        url_tags: list[str] = re.findall(
            r"(?:\[url(?:=|\])[^\]]*https?:\/\/passthepopcorn\.m[^\]]*\]|\bhttps?:\/\/passthepopcorn\.m[^\s]+)",
            desc,
            flags=re.IGNORECASE,
        )
        url_tags += [
            "".join(tag)
            for tag in re.findall(
                r"(\[url[\=\]]https?:\/\/hdbits\.o[^\]]+)([^\[]+)(\[\/url\])?",
                desc,
                flags=re.IGNORECASE,
            )
        ]
        for url_tag in url_tags:
            cleaned = re.sub(
                r"(\[url[\=\]]https?:\/\/passthepopcorn\.m[^\]]+])",
                "",
                url_tag,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                r"(\[url[\=\]]https?:\/\/hdbits\.o[^\]]+])",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
            desc = desc.replace(url_tag, cleaned.replace("[/url]", ""))
        desc = desc.replace(
            "http://passthepopcorn.me", "PassThePopcorn"
        ).replace("https://passthepopcorn.me", "PassThePopcorn")
        return desc.replace("http://hdbits.org", "HDBits").replace(
            "https://hdbits.org", "HDBits"
        )

    @staticmethod
    def _remove_ptp_specific_comparisons(desc: str) -> tuple[str, set[str]]:
        source_encode = re.findall(
            r"\[comparison=Source, Encode\][\s\S]*", desc, flags=re.IGNORECASE
        )
        source_vs = re.findall(
            r"Source Vs Encode:[\s\S]*", desc, flags=re.IGNORECASE
        )
        excluded_urls: set[str] = set()
        for block in source_encode + source_vs:
            excluded_urls.update(
                re.findall(
                    r"(https?:\/\/[^\s\[\]]+\.(?:png|jpg))",
                    block,
                    flags=re.IGNORECASE,
                )
            )
            desc = desc.replace(block, "")
        return desc, excluded_urls

    @staticmethod
    def _ptp_comparison_placeholders(
        desc: str, excluded_urls: set[str]
    ) -> tuple[str, str, list[str], list[str]]:
        comps = re.findall(
            r"\[comparison=[\s\S]*?\[\/comparison\]", desc, flags=re.IGNORECASE
        )
        hides = re.findall(
            r"\[hide[\s\S]*?\[\/hide\]", desc, flags=re.IGNORECASE
        )
        comps.extend(hides)
        nocomp = desc
        for url in excluded_urls:
            nocomp = nocomp.replace(url, "")
        placeholders: list[str] = []
        for index, comp in enumerate(comps):
            nocomp = nocomp.replace(comp, "")
            desc = desc.replace(comp, f"COMPARISON_PLACEHOLDER-{index} ")
            placeholders.append(comp)
        return desc, nocomp, hides, placeholders

    @staticmethod
    def _protect_ptp_links(desc: str) -> tuple[str, list[str]]:
        links: list[str] = re.findall(r"https?://\S+", desc)
        for index, link in enumerate(links):
            desc = desc.replace(link, f"__LINK_PLACEHOLDER_{index}__")
        return desc, links

    @staticmethod
    def _restore_ptp_links(desc: str, links: list[str]) -> str:
        for index, link in enumerate(links):
            desc = desc.replace(f"__LINK_PLACEHOLDER_{index}__", link)
        return desc

    @staticmethod
    def _remove_ptp_bdmv_metadata(desc: str) -> str:
        desc = re.sub(r"\[mediainfo\][\s\S]*?\[\/mediainfo\]", "", desc)
        patterns = (
            r"DISC INFO:[\s\S]*?(\n\n|$)",
            r"Disc Title:[\s\S]*?(\n\n|$)",
            r"Disc Size:[\s\S]*?(\n\n|$)",
            r"Protection:[\s\S]*?(\n\n|$)",
            r"BD-Java:[\s\S]*?(\n\n|$)",
            r"BDInfo:[\s\S]*?(\n\n|$)",
            r"PLAYLIST REPORT:[\s\S]*?(?=\n\n|$)",
            r"Name:[\s\S]*?(\n\n|$)",
            r"Length:[\s\S]*?(\n\n|$)",
            r"Size:[\s\S]*?(\n\n|$)",
            r"Total Bitrate:[\s\S]*?(\n\n|$)",
            r"VIDEO:[\s\S]*?(?=\n\n|$)",
            r"AUDIO:[\s\S]*?(?=\n\n|$)",
            r"SUBTITLES:[\s\S]*?(?=\n\n|$)",
            r"Codec\s+Bitrate\s+Description[\s\S]*?(?=\n\n|$)",
            r"Codec\s+Language\s+Bitrate\s+Description[\s\S]*?(?=\n\n|$)",
        )
        for pattern in patterns:
            desc = re.sub(pattern, "", desc, flags=re.IGNORECASE)
        return desc

    @classmethod
    def _remove_ptp_file_metadata(cls, desc: str) -> tuple[str, list[str]]:
        desc = re.sub(r"\[mediainfo\][\s\S]*?\[\/mediainfo\]", "", desc)
        block_patterns = (
            r"(^general\nunique)(.*?)^$",
            r"(^general\ncomplete)(.*?)^$",
            r"(^(Format[\s]{2,}:))(.*?)^$",
            r"(^(video|audio|text)( #\d+)?\nid)(.*?)^$",
        )
        flags = re.MULTILINE | re.IGNORECASE | re.DOTALL
        for pattern in block_patterns:
            desc = re.sub(pattern, "", desc, flags=flags)
        desc = re.sub(
            r"(^(menu)( #\d+)?\n)(.*?)^$", "", f"{desc}\n\n", flags=flags
        )
        desc, links = cls._protect_ptp_links(desc)
        desc = re.sub(
            r"\[b\](.*?)(Matroska|DTS|AVC|x264|Progressive|23\.976 fps|16:9|[0-9]+x[0-9]+|[0-9]+ MiB|[0-9]+ Kbps|[0-9]+ bits|cabac=.*?/ aq=.*?|\d+\.\d+ Mbps)\[/b\]",
            "",
            desc,
            flags=re.IGNORECASE | re.DOTALL,
        )
        desc = re.sub(
            r"(Matroska|DTS|AVC|x264|Progressive|23\.976 fps|16:9|[0-9]+x[0-9]+|[0-9]+ MiB|[0-9]+ Kbps|[0-9]+ bits|cabac=.*?/ aq=.*?|\d+\.\d+ Mbps|[0-9]+\s+channels|[0-9]+\.[0-9]+\s+KHz|[0-9]+ KHz|[0-9]+\s+bits)",
            "",
            desc,
            flags=re.IGNORECASE | re.DOTALL,
        )
        desc = re.sub(
            r"\[u\](Format|Bitrate|Channels|Sampling Rate|Resolution):\[/u\]\s*\d*.*?",
            "",
            desc,
            flags=re.IGNORECASE,
        )
        desc = re.sub(
            r"^\s*\d+\s*(channels|KHz|bits)\s*$",
            "",
            desc,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        desc = re.sub(r"^\s+$", "", desc, flags=re.MULTILINE)
        return re.sub(r"\n{2,}", "\n", desc), links

    @classmethod
    def _remove_ptp_media_metadata(
        cls, desc: str, is_disc: str
    ) -> tuple[str, list[str]]:
        if is_disc == "DVD":
            return re.sub(
                r"\[mediainfo\][\s\S]*?\[\/mediainfo\]", "", desc
            ), []
        if is_disc == "BDMV":
            return cls._remove_ptp_bdmv_metadata(desc), []
        return cls._remove_ptp_file_metadata(desc)

    @staticmethod
    def _clean_ptp_common_tags(desc: str) -> str:
        desc = re.sub(r"\[quote.*?\]", "[code]", desc).replace(
            "[/quote]", "[/code]"
        )
        desc = re.sub(r"\[align=.*?\]", "", desc).replace("[/align]", "")
        desc = re.sub(r"\[size=.*?\]", "", desc).replace("[/size]", "")
        desc = re.sub(r"\[video\][\s\S]*?\[\/video\]", "", desc)
        desc = re.sub(r"\[staff[\s\S]*?\[\/staff\]", "", desc)
        for tag in (
            "[movie]",
            "[/movie]",
            "[artist]",
            "[/artist]",
            "[user]",
            "[/user]",
            "[indent]",
            "[/indent]",
            "[size]",
            "[/size]",
            "[hr]",
        ):
            desc = desc.replace(tag, "")
        desc = re.sub(
            r"\[img\][\s\S]*?\[\/img\]", "", desc, flags=re.IGNORECASE
        )
        return re.sub(r"\[img=[\s\S]*?\]", "", desc, flags=re.IGNORECASE)

    @staticmethod
    def _extract_ptp_loose_images(
        desc: str, nocomp: str, excluded_urls: set[str]
    ) -> tuple[str, list[dict[str, Any]]]:
        loose_images = re.findall(
            r"(https?:\/\/[^\s\[\]]+\.(?:png|jpg))",
            nocomp,
            flags=re.IGNORECASE,
        )
        images: list[dict[str, Any]] = []
        for img_url in loose_images:
            if img_url in excluded_urls:
                continue
            images.append(
                {"img_url": img_url, "raw_url": img_url, "web_url": img_url}
            )
            desc = desc.replace(img_url, "")
        return desc, images

    @staticmethod
    def _restore_ptp_comparisons(desc: str, placeholders: list[str]) -> str:
        for index, comp in enumerate(placeholders):
            comp = re.sub(r"\[\/?img[\s\S]*?\]", "", comp, flags=re.IGNORECASE)
            desc = desc.replace(f"COMPARISON_PLACEHOLDER-{index} ", comp)
        return desc

    def clean_ptp_description(
        self, desc: str, is_disc: str
    ) -> tuple[str, list[dict[str, Any]]]:
        desc = self._normalized_description(desc.replace("&bull;", "-"))
        desc = self._clean_ptp_private_links(desc)
        desc, excluded_urls = self._remove_ptp_specific_comparisons(desc)
        desc, nocomp, hides, placeholders = self._ptp_comparison_placeholders(
            desc, excluded_urls
        )
        desc, links = self._remove_ptp_media_metadata(desc, is_disc)
        desc = self._restore_ptp_links(desc, links)
        desc = self._clean_ptp_common_tags(desc)
        desc, imagelist = self._extract_ptp_loose_images(
            desc, nocomp, excluded_urls
        )
        desc = self._restore_ptp_comparisons(desc, placeholders)
        desc = self.convert_collapse_to_comparison(desc, "hide", hides)
        desc = re.sub("\n\n+", "\n\n", desc.strip("\n"))
        if not desc.replace("\n", "").strip():
            return "", imagelist
        if self.is_only_bbcode(desc):
            return "", imagelist
        return desc, imagelist

    @staticmethod
    def _unit3d_site_parts(site: str) -> tuple[str, str]:
        netloc = urllib.parse.urlparse(site).netloc
        return netloc, netloc.split(".")[0]

    @staticmethod
    def _remove_unit3d_site_links(
        desc: str, site_netloc: str, site_domain: str
    ) -> str:
        site_regex = (
            rf"(\[url[\=\]]https?:\/\/{site_domain}\.[^\/\]]+\/[^\]]+])"
            r"([^\[]+)(\[\/url\])?"
        )
        for match in re.findall(site_regex, desc):
            site_url_tag = "".join(match)
            url_tag_regex = (
                rf"(\[url[\=\]]https?:\/\/{site_domain}\.[^\/\]]+[^\]]+])"
            )
            cleaned = re.sub(url_tag_regex, "", site_url_tag).replace(
                "[/url]", ""
            )
            desc = desc.replace(site_url_tag, cleaned)
        return desc.replace(site_netloc, site_domain)

    @staticmethod
    def _protect_unit3d_spoilers(desc: str) -> tuple[str, list[str]]:
        spoilers = re.findall(r"\[spoiler[\s\S]*?\[\/spoiler\]", desc)
        placeholders: list[str] = []
        for index, spoiler in enumerate(spoilers):
            desc = desc.replace(spoiler, f"SPOILER_PLACEHOLDER-{index} ")
            placeholders.append(spoiler)
        return desc, placeholders

    @staticmethod
    def _extract_unit3d_wrapped_images(
        desc: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        pattern = (
            r"\[url=(https?://[^\]]+)\]\[img[^\]]*\](.*?)\[/img\]\[/url\]"
        )
        matches = re.findall(pattern, desc, flags=re.IGNORECASE)
        images: list[dict[str, Any]] = []
        for web_url, img_url in matches:
            images.append(
                {
                    "img_url": img_url.strip(),
                    "raw_url": img_url.strip(),
                    "web_url": web_url.strip(),
                }
            )
            desc = re.sub(
                rf"\[url={re.escape(web_url)}\]\[img[^\]]*\]"
                rf"{re.escape(img_url)}\[/img\]\[/url\]",
                "",
                desc,
                flags=re.IGNORECASE,
            )
        return desc, images

    @staticmethod
    def _unit3d_image_known(
        images: list[dict[str, Any]], img_url: str
    ) -> bool:
        return any(image["img_url"] == img_url for image in images)

    @classmethod
    def _extract_unit3d_standalone_images(
        cls, desc: str, images: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        img_tags = re.findall(
            r"\[img[^\]]*\](.*?)\[/img\]", desc, re.IGNORECASE
        )
        for img_url in img_tags:
            img_url = img_url.strip()
            if not cls._unit3d_image_known(images, img_url):
                images.append(
                    {
                        "img_url": img_url,
                        "raw_url": img_url,
                        "web_url": img_url,
                    }
                )
            desc = re.sub(
                rf"\[img[^\]]*\]{re.escape(img_url)}\[/img\]",
                "",
                desc,
                flags=re.IGNORECASE,
            )
        return desc, images

    @staticmethod
    def _unit3d_bot_image_urls() -> tuple[str, ...]:
        return (
            "https://blutopia.xyz/favicon.ico",
            "https://i.ibb.co/2NVWb0c/uploadrr.webp",
            "https://blutopia/favicon.ico",
        )

    @classmethod
    def _unit3d_image_allowed(cls, image: dict[str, Any]) -> bool:
        img_url = image["img_url"]
        if img_url in cls._unit3d_bot_image_urls():
            return False
        return re.search(r"thumbs", img_url, re.IGNORECASE) is None

    @classmethod
    def _filter_unit3d_images(
        cls, images: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return [image for image in images if cls._unit3d_image_allowed(image)]

    @staticmethod
    def _clean_unit3d_center(center: str) -> str:
        cleaned = re.sub(r"\[center\]\s*\[\/center\]", "", center)
        cleaned = re.sub(r"\[center\]\s+", "[center]", cleaned)
        return re.sub(r"\s*\[\/center\]", "[/center]", cleaned)

    @classmethod
    def _clean_unit3d_centers(cls, desc: str) -> str:
        centers = re.findall(r"\[center[\s\S]*?\[\/center\]", desc)
        for center in centers:
            cleaned = cls._clean_unit3d_center(center)
            replacement = (
                "" if cleaned == "[center][/center]" else cleaned.strip()
            )
            desc = desc.replace(center, replacement)
        return desc

    @staticmethod
    def _unit3d_bot_signature_pattern() -> str:
        return r"""
            \[center\]\s*\[img=\d+\]https:\/\/blutopia\.xyz\/favicon\.ico\[\/img\]\s*\[b\]
            Uploaded\sUsing\s\[url=https:\/\/github\.com\/HDInnovations\/UNIT3D\]UNIT3D\[\/url\]\s
            Auto\sUploader\[\/b\]\s*\[img=\d+\]https:\/\/blutopia\.xyz\/favicon\.ico\[\/img\]\s*\[\/center\]|
            \[center\]\s*\[b\]Uploaded\sUsing\s\[url=https:\/\/github\.com\/HDInnovations\/UNIT3D\]UNIT3D\[\/url\]
            \sAuto\sUploader\[\/b\]\s*\[\/center\]|
            \[center\]\[url=https:\/\/github\.com\/z-ink\/uploadrr\]\[img=\d+\]https:\/\/i\.ibb\.co\/2NVWb0c\/uploadrr\.webp\[\/img\]\[\/url\]\[\/center\]|
            \n\[center\]\[url=https:\/\/github\.com\/edge20200\/Only-Uploader\]Powered\sby\s
            Only-Uploader\[\/url\]\[\/center\]|
            \[center\]\[url=\/torrents\?perPage=\d+&name=[^\]]*\]\[\/url\]\[\/center\]
        """

    @staticmethod
    def _unit3d_ua_signature_pattern() -> str:
        return r"""
            ^[ \t]*
            \[(?:right|center|align=(?:right|center))\][ \t]*(?:\n[ \t]*)?
            \[url=https:\/\/github\.com\/[^\]]*\/Upload-Assistant\][ \t]*(?:\n[ \t]*)?
            (?:\[size=\d+\][ \t]*(?:\n[ \t]*)?)?
            (?:Shared\s+with|Compartilhado\s+com)\s+Upload[-\s]+Assistant
            (?:[ \t]+v?\d+(?:[.+-]\d+)*)?
            (?:[ \t]+\(fork\))?
            [ \t]*(?:\n[ \t]*)?
            (?:\[\/size\][ \t]*(?:\n[ \t]*)?)?
            \[\/url\][ \t]*(?:\n[ \t]*)?
            \[\/(?:right|center|align)\][ \t]*$
        """

    @classmethod
    def _remove_unit3d_signatures(cls, desc: str) -> str:
        desc = re.sub(
            cls._unit3d_bot_signature_pattern(),
            "",
            desc,
            flags=re.IGNORECASE | re.VERBOSE,
        )
        desc = re.sub(
            r"\[center\]\[b\]\[size=\d+\]🖌️\[/size\]\[/b\][\s\S]*?"
            r"This is an internal release which was first released exclusively on Aither\."
            r"[\s\S]*?🍻 Cheers to all the Aither.*?\[/center\]",
            "",
            desc,
            flags=re.IGNORECASE,
        )
        desc = re.sub(
            r"\[center\].*Created by.*Upload Assistant.*\[\/center\]",
            "",
            desc,
            flags=re.IGNORECASE,
        )
        desc = re.sub(
            r"\[right\].*Created by.*Upload Assistant.*\[\/right\]",
            "",
            desc,
            flags=re.IGNORECASE,
        )
        return re.sub(
            cls._unit3d_ua_signature_pattern(),
            "",
            desc,
            flags=re.IGNORECASE | re.MULTILINE | re.VERBOSE,
        )

    @staticmethod
    def _remove_unit3d_leftover_images(desc: str) -> str:
        desc = re.sub(
            r"\[img\][\s\S]*?\[\/img\]", "", desc, flags=re.IGNORECASE
        )
        return re.sub(r"\[img=[\s\S]*?\]", "", desc, flags=re.IGNORECASE)

    @staticmethod
    def _restore_unit3d_spoilers(desc: str, spoilers: list[str]) -> str:
        for index, spoiler in enumerate(spoilers):
            desc = desc.replace(f"SPOILER_PLACEHOLDER-{index} ", spoiler)
        return desc

    def clean_unit3d_description(
        self, desc: str, site: str
    ) -> tuple[str, list[dict[str, Any]]]:
        desc = self._normalized_description(desc)
        site_netloc, site_domain = self._unit3d_site_parts(site)
        desc = self._remove_unit3d_site_links(desc, site_netloc, site_domain)
        desc, spoilers = self._protect_unit3d_spoilers(desc)
        desc, imagelist = self._extract_unit3d_wrapped_images(desc)
        desc, imagelist = self._extract_unit3d_standalone_images(
            desc, imagelist
        )
        imagelist = self._filter_unit3d_images(imagelist)
        desc = self._clean_unit3d_centers(desc)
        desc = self._remove_unit3d_signatures(desc)
        desc = self._remove_unit3d_leftover_images(desc)
        desc = self._restore_unit3d_spoilers(desc, spoilers).rstrip()
        if desc.replace("\n", "") == "":
            return "", imagelist
        if self.is_only_bbcode(desc):
            return "", imagelist
        return desc, imagelist

    def is_only_bbcode(self, desc: str) -> bool:
        # Remove all BBCode tags
        text = re.sub(r"\[/?[a-zA-Z0-9]+(?:=[^\]]*)?\]", "", desc)
        # Remove whitespace and newlines
        text = text.strip()
        # If nothing left, it's only BBCode
        return not text

    def convert_pre_to_code(self, desc: str) -> str:
        desc = desc.replace("[pre]", "[code]")
        return desc.replace("[/pre]", "[/code]")

    def convert_code_to_pre(self, desc: str) -> str:
        desc = desc.replace("[code]", "[pre]")
        return desc.replace("[/code]", "[/pre]")

    def convert_hide_to_spoiler(self, desc: str) -> str:
        desc = desc.replace("[hide", "[spoiler")
        return desc.replace("[/hide]", "[/spoiler]")

    def convert_spoiler_to_hide(self, desc: str) -> str:
        desc = desc.replace("[spoiler", "[hide")
        return desc.replace("[/spoiler]", "[/hide]")

    def remove_hide(self, desc: str) -> str:
        return re.sub(
            r"\[\/?hide(?:=[^\]]*)?\]", "", desc, flags=re.IGNORECASE
        )

    def convert_named_spoiler_to_named_hide(self, desc: str) -> str:
        """
        Converts [spoiler=Name] to [hide=Name]
        """
        desc = re.sub(
            r"\[spoiler=([^]]+)]", r"[hide=\1]", desc, flags=re.IGNORECASE
        )
        return desc.replace("[/spoiler]", "[/hide]")

    def remove_spoiler(self, desc: str) -> str:
        return re.sub(r"\[\/?spoiler[\s\S]*?\]", "", desc, flags=re.IGNORECASE)

    def remove_color(self, desc: str) -> str:
        """
        Removes [color=...] and [/color] tags.
        """
        pattern = r"\[/?color(?:=[^\]]*)?\]"
        return re.sub(pattern, "", desc, flags=re.IGNORECASE)

    def convert_named_spoiler_to_normal_spoiler(self, desc: str) -> str:
        return re.sub(
            r"(\[spoiler=[^]]+])", "[spoiler]", desc, flags=re.IGNORECASE
        )

    def convert_spoiler_to_code(self, desc: str) -> str:
        desc = desc.replace("[spoiler", "[code")
        return desc.replace("[/spoiler]", "[/code]")

    def convert_code_to_quote(self, desc: str) -> str:
        desc = desc.replace("[code", "[quote")
        return desc.replace("[/code]", "[/quote]")

    def remove_img_resize(self, desc: str) -> str:
        """
        Converts [img=number] or any other parameters to just [img]
        """
        return re.sub(r"\[img(?:[^\]]*)\]", "[img]", desc, flags=re.IGNORECASE)

    def remove_extra_lines(self, desc: str) -> str:
        """
        Removes more than 2 consecutive newlines
        """
        return re.sub(r"\n{3,}", "\n\n", desc)

    def convert_to_align(self, desc: str) -> str:
        """
        Converts [right], [left], [center] to [align=right], [align=left], [align=center]
        """
        desc = re.sub(
            r"\[(right|center|left)\]", lambda m: f"[align={m.group(1)}]", desc
        )
        return re.sub(r"\[/(right|center|left)\]", "[/align]", desc)

    def remove_sup(self, desc: str) -> str:
        """
        Removes [sup] tags
        """
        return desc.replace("[sup]", "").replace("[/sup]", "")

    def remove_sub(self, desc: str) -> str:
        """
        Removes [sub] tags
        """
        return desc.replace("[sub]", "").replace("[/sub]", "")

    def remove_list(self, desc: str) -> str:
        """
        Removes [list] tags
        """
        return desc.replace("[list]", "").replace("[/list]", "")

    @staticmethod
    def _comparison_blocks(desc: str) -> list[str]:
        return re.findall(r"\[comparison=[\s\S]*?\[\/comparison\]", desc)

    @staticmethod
    def _comparison_sources(comp: str, compact: bool) -> list[str]:
        header = comp.split("]", 1)[0].replace("[comparison=", "")
        if compact:
            return header.replace(" ", "").split(",")
        return re.split(r"\s*,\s*", header.strip())

    @staticmethod
    def _comparison_images(comp: str) -> list[str]:
        body = (
            comp.split("]", 1)[1]
            .replace("[/comparison]", "")
            .replace(",", "\n")
            .replace(" ", "\n")
        )
        return re.findall(
            r"(https?:\/\/.*\.(?:png|jpg))", body, flags=re.IGNORECASE
        )

    @staticmethod
    def _comparison_image_size(max_width: int, source_count: int) -> int:
        return min(int(max_width / source_count), 350)

    @staticmethod
    def _comparison_rows(
        images: list[str], source_count: int, image_size: int
    ) -> str:
        line: list[str] = []
        output: list[str] = []
        for image in images:
            image = image.strip()
            if not image:
                continue
            line.append(f"[url={image}][img={image_size}]{image}[/img][/url]")
            if len(line) == source_count:
                output.append("".join(line))
                line = []
        return "\n".join(output)

    @classmethod
    def _comparison_parts(
        cls, comp: str, max_width: int, compact: bool
    ) -> tuple[list[str], str]:
        sources = cls._comparison_sources(comp, compact)
        images = cls._comparison_images(comp)
        image_size = cls._comparison_image_size(max_width, len(sources))
        rows = cls._comparison_rows(images, len(sources), image_size)
        return sources, rows

    @classmethod
    def _comparison_collapse_bbcode(cls, comp: str, max_width: int) -> str:
        sources, rows = cls._comparison_parts(comp, max_width, True)
        return (
            f"[spoiler={' vs '.join(sources)}]"
            f"[center]{' | '.join(sources)}[/center]\n{rows}[/spoiler]"
        )

    @classmethod
    def _comparison_centered_bbcode(cls, comp: str, max_width: int) -> str:
        sources, rows = cls._comparison_parts(comp, max_width, False)
        return f"[center]{' | '.join(sources)}\n{rows}[/center]"

    def convert_comparison_to_collapse(self, desc: str, max_width: int) -> str:
        for comp in self._comparison_blocks(desc):
            desc = desc.replace(
                comp, self._comparison_collapse_bbcode(comp, max_width)
            )
        return desc

    def convert_comparison_to_centered(self, desc: str, max_width: int) -> str:
        for comp in self._comparison_blocks(desc):
            desc = desc.replace(
                comp, self._comparison_centered_bbcode(comp, max_width)
            )
        return desc

    @staticmethod
    def _collapse_image_urls(tag: str) -> list[str]:
        images = re.findall(
            r"\[img[\s\S]*?\[\/img\]", tag, flags=re.IGNORECASE
        )
        return [
            re.sub(
                r"\[img[\s\S]*\]",
                "",
                image.replace("[/img]", ""),
                flags=re.IGNORECASE,
            )
            for image in images
        ]

    @staticmethod
    def _collapse_sources(tag: str, spoiler_hide: str) -> str:
        if spoiler_hide == "spoiler":
            match = re.match(r"\[spoiler[\s\S]*?\]", tag)
            return match[0].replace("[spoiler=", "")[:-1] if match else ""
        if spoiler_hide == "hide":
            match = re.match(r"\[hide[\s\S]*?\]", tag)
            return match[0].replace("[hide=", "")[:-1] if match else ""
        return ""

    @staticmethod
    def _normalized_comparison_sources(sources: str) -> list[str]:
        sources = re.sub("comparison", "", sources, flags=re.IGNORECASE)
        for separator in ("vs", ",", "|"):
            sources = "$".join(sources.split(separator))
        return [source.strip() for source in sources.split("$")]

    @classmethod
    def _collapse_comparison_bbcode(
        cls, tag: str, spoiler_hide: str
    ) -> str | None:
        images = cls._collapse_image_urls(tag)
        if len(images) < 6:
            return None
        sources = cls._collapse_sources(tag, spoiler_hide)
        if not sources:
            return None
        final_sources = cls._normalized_comparison_sources(sources)
        return (
            f"[comparison={', '.join(final_sources)}]"
            f"{'\n'.join(images)}[/comparison]"
        )

    def convert_collapse_to_comparison(
        self, desc: str, spoiler_hide: str, collapses: list[str]
    ) -> str:
        for tag in collapses:
            replacement = self._collapse_comparison_bbcode(tag, spoiler_hide)
            if replacement is not None:
                desc = desc.replace(tag, replacement)
        return desc
