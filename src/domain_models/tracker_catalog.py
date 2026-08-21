"""Immutable tracker catalog used by domain and delivery validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class TrackerDefinition:
    name: str
    auth_type: str
    supported_categories: tuple[str, ...]
    comment_hosts: tuple[str, ...]
    is_usenet: bool = False


_DEFINITIONS = {
    "1PTBA": TrackerDefinition(
        "1PTBA", "cookies", ("TV", "MOVIE"), ("1ptba.com",), False
    ),
    "AITHER": TrackerDefinition(
        "AITHER", "unit3d_api", ("TV", "MOVIE"), ("aither.cc",), False
    ),
    "ALPHARATIO": TrackerDefinition(
        "ALPHARATIO",
        "cookies",
        ("TV", "MOVIE"),
        ("alpharatio.cc", "tracker.alpharatio"),
        False,
    ),
    "AMIGOSSHARE": TrackerDefinition(
        "AMIGOSSHARE",
        "cookies",
        ("TV", "MOVIE", "BOOK", "GAME"),
        ("cliente.amigos-share.club", "amigos-share.club"),
        False,
    ),
    "ANTHELION": TrackerDefinition(
        "ANTHELION",
        "other_api",
        ("MOVIE",),
        ("anthelion.me", "tracker.anthelion.me"),
        False,
    ),
    "ASIANCINEMA": TrackerDefinition(
        "ASIANCINEMA", "unit3d_api", ("TV", "MOVIE"), ("eiga.moi",), False
    ),
    "AURA4K": TrackerDefinition(
        "AURA4K", "unit3d_api", ("TV", "MOVIE"), ("aura4k.net",), False
    ),
    "AVISTAZ": TrackerDefinition(
        "AVISTAZ",
        "cookies",
        ("TV", "MOVIE"),
        ("avistaz.to", "tracker.avistaz.to"),
        False,
    ),
    "BEYONDHD": TrackerDefinition(
        "BEYONDHD",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("beyond-hd.me", "tracker.beyond-hd.me"),
        False,
    ),
    "BITHDTV": TrackerDefinition(
        "BITHDTV", "other_api", ("TV", "MOVIE"), ("www.bit-hdtv.com",), False
    ),
    "BITPORN": TrackerDefinition(
        "BITPORN", "unit3d_api", ("XXX",), ("bitporn.eu",), False
    ),
    "BJSHARE": TrackerDefinition(
        "BJSHARE",
        "cookies",
        ("TV", "MOVIE", "BOOK", "GAME"),
        ("bj-share.info", "tracker.bj-share.info"),
        False,
    ),
    "BLUTOPIA": TrackerDefinition(
        "BLUTOPIA", "unit3d_api", ("TV", "MOVIE"), ("blutopia.cc",), False
    ),
    "BRASILTRACKER": TrackerDefinition(
        "BRASILTRACKER",
        "cookies",
        ("TV", "MOVIE", "BOOK", "GAME"),
        ("brasiltracker.org", "t.brasiltracker.org"),
        False,
    ),
    "CAPYBARABR": TrackerDefinition(
        "CAPYBARABR",
        "unit3d_api",
        ("TV", "MOVIE", "BOOK", "GAME"),
        ("capybarabr.com",),
        False,
    ),
    "CATHODERAYTUBE": TrackerDefinition(
        "CATHODERAYTUBE",
        "cookies",
        ("MOVIE", "TV", "GAME"),
        ("www.cathode-ray.tube", "signal.cathode-ray.tube"),
        False,
    ),
    "CINEMATIK": TrackerDefinition(
        "CINEMATIK", "unit3d_api", ("TV", "MOVIE"), ("cinematik.net",), False
    ),
    "CINEMAZ": TrackerDefinition(
        "CINEMAZ",
        "cookies",
        ("TV", "MOVIE"),
        ("cinemaz.to", "tracker.cinemaz.to"),
        False,
    ),
    "CURUPIRA": TrackerDefinition(
        "CURUPIRA",
        "other_api",
        ("TV", "MOVIE", "GAME", "BOOK"),
        ("curupira.cc",),
        True,
    ),
    "DARKPEERS": TrackerDefinition(
        "DARKPEERS",
        "unit3d_api",
        ("TV", "MOVIE", "BOOK", "GAME", "MUSIC"),
        ("darkpeers.org",),
        False,
    ),
    "DESITORRENTS": TrackerDefinition(
        "DESITORRENTS", "unit3d_api", ("TV", "MOVIE"), ("torrent.desi",), False
    ),
    "DIGITALCORE": TrackerDefinition(
        "DIGITALCORE",
        "other_api",
        ("TV", "MOVIE", "BOOK", "GAME", "MUSIC"),
        (
            "digitalcore.club",
            "tracker.digitalcore.club",
            "trackerprxy.digitalcore.club",
        ),
        False,
    ),
    "DRUNKENSLUG": TrackerDefinition(
        "DRUNKENSLUG",
        "other_api",
        ("TV", "MOVIE", "GAME", "BOOK"),
        ("drunkenslug.com",),
        True,
    ),
    "EMUWAREZ": TrackerDefinition(
        "EMUWAREZ", "unit3d_api", ("TV", "MOVIE"), ("emuwarez.com",), False
    ),
    "FILELIST": TrackerDefinition(
        "FILELIST",
        "cookies",
        ("TV", "MOVIE"),
        ("filelist.io", "reactor.filelist", "reactor.thefl.org"),
        False,
    ),
    "FUNFILE": TrackerDefinition(
        "FUNFILE",
        "cookies",
        ("TV", "MOVIE"),
        ("www.funfile.org", "tracker.funfile.org"),
        False,
    ),
    "GREATPOSTERWALL": TrackerDefinition(
        "GREATPOSTERWALL",
        "other_api",
        ("MOVIE",),
        ("greatposterwall.com", "tracker.greatposterwall.com"),
        False,
    ),
    "HAWKEUNO": TrackerDefinition(
        "HAWKEUNO", "unit3d_api", ("TV", "MOVIE"), ("hawke.uno",), False
    ),
    "HDBITS": TrackerDefinition(
        "HDBITS",
        "cookies",
        ("TV", "MOVIE"),
        ("hdbits.org", "tracker.hdbits.org"),
        False,
    ),
    "HDSPACE": TrackerDefinition(
        "HDSPACE",
        "cookies",
        ("TV", "MOVIE"),
        ("hd-space.org", "hd-space.pw"),
        False,
    ),
    "HDTORRENTS": TrackerDefinition(
        "HDTORRENTS",
        "cookies",
        ("TV", "MOVIE"),
        ("hd-torrents.org", "hdts-announce.ru"),
        False,
    ),
    "HOMIEHELPDESK": TrackerDefinition(
        "HOMIEHELPDESK",
        "unit3d_api",
        ("TV", "MOVIE", "BOOK", "GAME", "MUSIC"),
        ("homiehelpdesk.net",),
        False,
    ),
    "IMMORTALSEED": TrackerDefinition(
        "IMMORTALSEED",
        "cookies",
        ("TV", "MOVIE", "BOOK", "MUSIC", "GAME"),
        ("immortalseed.me",),
        False,
    ),
    "INFINITYHD": TrackerDefinition(
        "INFINITYHD", "unit3d_api", ("TV", "MOVIE"), ("infinityhd.net",), False
    ),
    "IPTORRENTS": TrackerDefinition(
        "IPTORRENTS",
        "cookies",
        ("TV", "MOVIE", "BOOK", "GAME", "MUSIC"),
        (
            "iptorrents.com",
            "ssl.empirehost.me",
            "routing.bgp.technology",
            "127.0.0.1.stackoverflow.tech",
        ),
        False,
    ),
    "ITATORRENTS": TrackerDefinition(
        "ITATORRENTS",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("itatorrents.xyz",),
        False,
    ),
    "LAJIDUI": TrackerDefinition(
        "LAJIDUI", "cookies", ("TV", "MOVIE"), ("pt.lajidui.top",), False
    ),
    "LASTDIGITALUNDERGROUND": TrackerDefinition(
        "LASTDIGITALUNDERGROUND",
        "unit3d_api",
        ("TV", "MOVIE", "BOOK"),
        ("theldu.to",),
        False,
    ),
    "LATTEAM": TrackerDefinition(
        "LATTEAM",
        "unit3d_api",
        ("TV", "MOVIE", "BOOK"),
        ("lat-team.com",),
        False,
    ),
    "LEMONHD": TrackerDefinition(
        "LEMONHD", "cookies", ("TV", "MOVIE"), ("lemonhd.net",), False
    ),
    "LOCADORA": TrackerDefinition(
        "LOCADORA", "unit3d_api", ("TV", "MOVIE"), ("locadora.cc",), False
    ),
    "LONGPT": TrackerDefinition(
        "LONGPT", "cookies", ("TV", "MOVIE"), ("longpt.org",), False
    ),
    "LST": TrackerDefinition(
        "LST",
        "unit3d_api",
        ("TV", "MOVIE", "BOOK", "MUSIC", "XXX"),
        ("lst.gg",),
        False,
    ),
    "LUMINARR": TrackerDefinition(
        "LUMINARR", "unit3d_api", ("TV", "MOVIE"), ("luminarr.me",), False
    ),
    "MAKINGOFF": TrackerDefinition(
        "MAKINGOFF", "cookies", ("MOVIE",), ("www.makingoff.org",), False
    ),
    "MIDNIGHTSCENE": TrackerDefinition(
        "MIDNIGHTSCENE",
        "unit3d_api",
        ("TV", "MOVIE", "GAME", "MUSIC"),
        ("midnightscene.cc",),
        False,
    ),
    "MTEAM": TrackerDefinition(
        "MTEAM",
        "other_api",
        ("TV", "MOVIE"),
        (
            "kp.m-team.cc",
            "tracker.m-team.cc",
            "tra1.m-team.cc",
            "tracker.m-team.io",
            "tra1.m-team.io",
            "tra99.manfuz.co",
        ),
        False,
    ),
    "NEBULANCE": TrackerDefinition(
        "NEBULANCE",
        "other_api",
        ("TV",),
        ("nebulance.io", "tracker.nebulance"),
        False,
    ),
    "NORDICQUALITY": TrackerDefinition(
        "NORDICQUALITY",
        "unit3d_api",
        ("TV", "MOVIE", "MUSIC", "BOOK", "GAME"),
        ("nordicq.org",),
        False,
    ),
    "NZBGEEK": TrackerDefinition(
        "NZBGEEK",
        "other_api",
        ("TV", "MOVIE", "GAME", "BOOK", "MUSIC"),
        ("api.nzbgeek.info",),
        True,
    ),
    "OLDTOONSWORLD": TrackerDefinition(
        "OLDTOONSWORLD",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("oldtoons.world",),
        False,
    ),
    "ONLYENCODES": TrackerDefinition(
        "ONLYENCODES",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("onlyencodes.cc",),
        False,
    ),
    "ORPHEUS": TrackerDefinition(
        "ORPHEUS",
        "other_api",
        ("MUSIC",),
        ("orpheus.network", "home.opsfet.ch"),
        False,
    ),
    "PASSTHEPOPCORN": TrackerDefinition(
        "PASSTHEPOPCORN", "", ("MOVIE",), ("passthepopcorn.me",), False
    ),
    "PEERGARDEN": TrackerDefinition(
        "PEERGARDEN",
        "unit3d_api",
        ("TV", "MOVIE", "GAME", "BOOK", "MUSIC"),
        ("peergarden.org", "peergarden"),
        False,
    ),
    "POLISHTORRENT": TrackerDefinition(
        "POLISHTORRENT",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("polishtorrent.top",),
        False,
    ),
    "PORTUGAS": TrackerDefinition(
        "PORTUGAS", "unit3d_api", ("TV", "MOVIE"), ("portugas.org",), False
    ),
    "PRIVATEHD": TrackerDefinition(
        "PRIVATEHD",
        "cookies",
        ("TV", "MOVIE"),
        ("privatehd.to", "tracker.privatehd"),
        False,
    ),
    "PTCAFE": TrackerDefinition(
        "PTCAFE",
        "cookies",
        ("TV", "MOVIE"),
        ("ptcafe.club", "tracker.ptcafe.club"),
        False,
    ),
    "PTERCLUB": TrackerDefinition(
        "PTERCLUB", "cookies", ("TV", "MOVIE"), ("pterclub.net",), False
    ),
    "PTFANS": TrackerDefinition(
        "PTFANS", "cookies", ("TV", "MOVIE"), ("ptfans.cc",), False
    ),
    "PTGTK": TrackerDefinition(
        "PTGTK",
        "cookies",
        ("TV", "MOVIE"),
        ("pt.gtkpw.xyz", "t.myaltbox.com"),
        False,
    ),
    "PTSKIT": TrackerDefinition(
        "PTSKIT",
        "cookies",
        ("TV", "MOVIE"),
        ("www.ptskit.org", "tracker.ptskit.com"),
        False,
    ),
    "PTZONE": TrackerDefinition(
        "PTZONE", "cookies", ("TV", "MOVIE"), ("ptzone.xyz",), False
    ),
    "RACING4EVERYONE": TrackerDefinition(
        "RACING4EVERYONE",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("racing4everyone.eu",),
        False,
    ),
    "RAILGUNPT": TrackerDefinition(
        "RAILGUNPT",
        "cookies",
        ("TV", "MOVIE", "MUSIC", "GAME"),
        ("bilibili.download",),
        False,
    ),
    "RASTASTUGAN": TrackerDefinition(
        "RASTASTUGAN",
        "unit3d_api",
        ("TV", "MOVIE", "BOOK", "GAME", "MUSIC"),
        ("rastastugan.org",),
        False,
    ),
    "REELFLIX": TrackerDefinition(
        "REELFLIX",
        "unit3d_api",
        ("MOVIE",),
        ("reelflix.cc", "reelflix.xyz"),
        False,
    ),
    "RETROFLIX": TrackerDefinition(
        "RETROFLIX",
        "other_api",
        ("TV", "MOVIE"),
        ("retroflix.club", "peer.retroflix"),
        False,
    ),
    "RETROMOVIESCLUB": TrackerDefinition(
        "RETROMOVIESCLUB",
        "unit3d_api",
        ("MOVIE",),
        ("retro-movies.club",),
        False,
    ),
    "SAMARITANO": TrackerDefinition(
        "SAMARITANO",
        "unit3d_api",
        ("TV", "MOVIE", "BOOK", "GAME"),
        ("samaritano.cc",),
        False,
    ),
    "SEEDPOOL": TrackerDefinition(
        "SEEDPOOL",
        "unit3d_api",
        ("TV", "MOVIE", "BOOK", "GAME", "MUSIC"),
        ("seedpool.org",),
        False,
    ),
    "SHAREISLAND": TrackerDefinition(
        "SHAREISLAND",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("shareisland.org",),
        False,
    ),
    "SKIPTHECOMMERCIALS": TrackerDefinition(
        "SKIPTHECOMMERCIALS",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("skipthecommercials.xyz",),
        False,
    ),
    "SPEEDAPP": TrackerDefinition(
        "SPEEDAPP",
        "other_api",
        ("TV", "MOVIE", "BOOK", "GAME", "MUSIC"),
        ("speedapp.io", "speedapp"),
        False,
    ),
    "SUIO": TrackerDefinition(
        "SUIO",
        "other_api",
        ("MOVIE", "TV", "GAME", "BOOK", "XXX"),
        ("suio.cc",),
        True,
    ),
    "SWARMAZON": TrackerDefinition(
        "SWARMAZON", "other_api", ("TV", "MOVIE"), ("swarmazon.club",), False
    ),
    "THELEACHZONE": TrackerDefinition(
        "THELEACHZONE",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("tlzdigital.com",),
        False,
    ),
    "THEOLDSCHOOL": TrackerDefinition(
        "THEOLDSCHOOL",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("theoldschool.cc",),
        False,
    ),
    "TORRENTEROS": TrackerDefinition(
        "TORRENTEROS",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("torrenteros.org",),
        False,
    ),
    "TORRENTHR": TrackerDefinition(
        "TORRENTHR",
        "unit3d_api",
        ("TV", "MOVIE"),
        ("www.torrenthr.org", "torrenthr.org"),
        False,
    ),
    "TORRENTLEECH": TrackerDefinition(
        "TORRENTLEECH",
        "other_api",
        ("TV", "MOVIE", "BOOK", "GAME", "MUSIC"),
        (
            "www.torrentleech.org",
            "tracker.tleechreload",
            "tracker.torrentleech",
        ),
        False,
    ),
    "TOTHEGLORY": TrackerDefinition(
        "TOTHEGLORY", "cookies", ("TV", "MOVIE"), ("totheglory.im",), False
    ),
    "TVCHAOSUK": TrackerDefinition(
        "TVCHAOSUK", "other_api", ("TV", "MOVIE"), ("tvchaosuk.com",), False
    ),
    "ULCX": TrackerDefinition(
        "ULCX", "unit3d_api", ("TV", "MOVIE"), ("upload.cx",), False
    ),
    "UNWALLED": TrackerDefinition(
        "UNWALLED", "unit3d_api", ("PODCAST",), ("unwalled.cc",), False
    ),
    "UTOPIA": TrackerDefinition(
        "UTOPIA", "unit3d_api", ("TV", "MOVIE"), ("utp.to",), False
    ),
    "XINGYUNGEPT": TrackerDefinition(
        "XINGYUNGEPT",
        "cookies",
        ("TV", "MOVIE"),
        ("pt.xingyungept.org",),
        False,
    ),
    "YUSCENE": TrackerDefinition(
        "YUSCENE",
        "unit3d_api",
        ("TV", "MOVIE", "BOOK", "GAME", "MUSIC"),
        ("yu-scene.net",),
        False,
    ),
    "ZENITH": TrackerDefinition(
        "ZENITH",
        "unit3d_api",
        ("TV", "MOVIE", "BOOK", "GAME", "MUSIC"),
        ("znth.cx",),
        False,
    ),
}

TRACKER_DEFINITIONS: Mapping[str, TrackerDefinition] = MappingProxyType(
    _DEFINITIONS
)
KNOWN_TRACKERS = frozenset(TRACKER_DEFINITIONS)
USENET_TRACKERS = frozenset(
    name
    for name, definition in TRACKER_DEFINITIONS.items()
    if definition.is_usenet
)
