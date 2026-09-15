# -*- coding: utf-8 -*-
"""
SSRF গার্ড — MeTube-এর `app/url_guard.py` পোর্ট (stdlib-only)।

কেন দরকার: আমাদের `/api/player?id=<URL>` এবং `/api/downloads` ইউজার-দেওয়া URL নেয় এবং
সেটি yt-dlp/urllib-কে দিয়ে ফেচ করায়। গার্ড ছাড়া কেউ `http://169.254.169.254/…` (ক্লাউড
মেটাডেটা), `http://127.0.0.1:8080/…` বা ইন্টারনাল RFC1918 হোস্ট পাঠিয়ে সার্ভারকে
নিজের নেটওয়ার্ক পড়াতে পারে — এবং সেই রেসপন্স ডিভাইসে সার্ভ হয়ে ফিরে আসে।

দুই স্তর (MeTube-এর মতোই):
  ১) `validate_url` — ইনগ্রেসে সস্তা যাচাই (scheme allowlist, ব্লকড হোস্টনেম, DNS resolve
     করে প্রতিটি ঠিকানা global কি না যাচাই; resolve না হলে **fail closed**)।
  ২) `install_socket_guard` — কানেক্ট-টাইম `getaddrinfo` গার্ড: redirect/DNS-rebinding
     কভার করে। এটা নিজের প্রসেসে ইনস্টল করা যায়; yt-dlp সাবপ্রসেস Python নয়, তাই তার
     জন্য ইনগ্রেস-যাচাই + নেটওয়ার্ক আইসোলেশনই শেষ ভরসা (নিচে সীমাবদ্ধতা দেখুন)।

সীমাবদ্ধতা (সৎভাবে): yt-dlp সাবপ্রসেস, curl_cffi/`--impersonate`, এবং মিডিয়া URL যেগুলো
রিমোট ম্যানিফেস্ট থেকে আসে — এগুলো কানেক্ট-টাইম গার্ডের বাইরে। ইনগ্রেস যাচাই + `ALLOWED_HOSTS`
allowlist + Docker নেটওয়ার্ক আইসোলেশন = আসল ব্যাকস্টপ।
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.request
from typing import Iterable, List, Optional, Set, Tuple
from urllib.parse import urlsplit

log = logging.getLogger("url_guard")

_ALLOWED_SCHEMES = ("http", "https")
_BLOCKED_HOSTNAMES = ("localhost", "metadata.google.internal")
_SCHEME_DEFAULT_PORTS = {"http": 80, "https": 443,
                         "socks4": 1080, "socks4a": 1080, "socks5": 1080, "socks5h": 1080}

# IPv4-কে টানেল করা IPv6 ফর্মগুলো: `is_global` শুধু বাইরের ঠিকানা দেখে,
# তাই ভেতরের ইন্টারনাল IPv4 নিজের গুণে যাচাই করা দরকার।
_NAT64_WELL_KNOWN_PREFIX = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")
_UNUSABLE_IPV4 = ipaddress.ip_network("0.0.0.0/8")

_real_getaddrinfo = socket.getaddrinfo
_allowed_endpoints: Set[Tuple[str, Optional[int]]] = set()


def _hostname_is_blocked(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    for blocked in _BLOCKED_HOSTNAMES:
        if host == blocked or host.endswith("." + blocked):
            return True
    return False


def _normalise_ip(addr: str):
    """IPv4-mapped IPv6 (::ffff:169.254.169.254) খুলে ভেতরের IPv4 বিচার করা হয়।"""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip


def _tunnelled_ipv4(ip):
    """6to4 (2002::/16), Teredo (2001::/32), NAT64 (64:ff9b::/96), IPv4-compatible (::/96) খোলে।"""
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if ip in _NAT64_WELL_KNOWN_PREFIX or ip in _IPV4_COMPATIBLE:
        tunnelled = ipaddress.ip_address(int(ip) & 0xFFFFFFFF)
        return None if tunnelled in _UNUSABLE_IPV4 else tunnelled
    return None


def _ips_to_judge(addr: str) -> Tuple:
    """যে ঠিকানাগুলোর সবগুলো global হতে হবে: নিজে + টানেল করা IPv4 (থাকলে)।"""
    ip = _normalise_ip(addr)
    if ip is None:
        return ()
    tunnelled = _tunnelled_ipv4(ip)
    return (ip,) if tunnelled is None else (ip, tunnelled)


def address_is_global(addr: str) -> bool:
    ips = _ips_to_judge(addr)
    return bool(ips) and all(ip.is_global for ip in ips)


def endpoint_of(url: str) -> Optional[Tuple[str, Optional[int]]]:
    if not isinstance(url, str) or not url.strip():
        return None
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "//" + candidate          # bare host:port (*_proxy স্টাইল)
    try:
        parts = urlsplit(candidate)
        hostname, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not hostname:
        return None
    if port is None:
        port = _SCHEME_DEFAULT_PORTS.get(parts.scheme.lower())
    return (hostname.rstrip(".").lower(), port)


def collect_proxy_endpoints(proxy_urls: Iterable[str]) -> Set[Tuple[str, Optional[int]]]:
    candidates: List[str] = list(proxy_urls) + list(urllib.request.getproxies().values())
    return {ep for ep in map(endpoint_of, candidates) if ep is not None}


def _is_allowed_endpoint(host: Optional[str], port) -> bool:
    """অপারেটরের কনফিগার করা endpoint (প্রোক্সি/PO-token সার্ভার) হলে ছাড় — host-স্ট্রিং মিলিয়ে,
    resolved address মিলিয়ে নয় (নাহলে হোস্টাইল URL একই ঠিকানায় resolve করে ছাড় পেয়ে যাবে)।"""
    if not _allowed_endpoints or host is None:
        return False
    try:
        normalised_port = int(port) if isinstance(port, str) else port
    except ValueError:
        try:
            normalised_port = socket.getservbyname(port)
        except OSError:
            normalised_port = None
    return (str(host).rstrip(".").lower(), normalised_port) in _allowed_endpoints


def _guarded_getaddrinfo(host, *args, **kwargs):
    results = _real_getaddrinfo(host, *args, **kwargs)
    port = args[0] if args else kwargs.get("port")
    is_configured = _is_allowed_endpoint(host, port)
    allowed = [r for r in results
               if is_configured or all(ip.is_global for ip in _ips_to_judge(r[4][0]))]
    if not allowed:
        raise socket.gaierror("নন-গ্লোবাল ঠিকানায় সংযোগ প্রত্যাখ্যান: %r" % host)
    return allowed


def install_socket_guard(allow_private: bool = False, proxy_urls: Iterable[str] = (),
                         service_urls: Iterable[str] = (), allowed_hosts: Iterable[str] = ()) -> None:
    """কানেক্ট-টাইম গার্ড ইনস্টল (প্রসেস-ওয়াইড)। `allow_private=True` হলে কিছুই ইনস্টল হয় না।"""
    global _allowed_endpoints
    if allow_private:
        return
    proxy_endpoints = collect_proxy_endpoints(proxy_urls)
    service_endpoints = {ep for ep in map(endpoint_of, service_urls) if ep is not None}
    for host in allowed_hosts:                     # allowlist-এ দেওয়া গ্লোবাল হোস্ট
        endpoint = endpoint_of("http://%s" % host)
        if endpoint:
            service_endpoints.add(endpoint)
    service_endpoints -= proxy_endpoints
    _allowed_endpoints = proxy_endpoints | service_endpoints
    for label, endpoints in (("proxy", proxy_endpoints), ("service", service_endpoints)):
        for host, port in sorted(endpoints, key=lambda ep: (ep[0], ep[1] or 0)):
            log.info("কনফিগার করা %s endpoint ছাড় দেওয়া হলো: %s:%s", label, host, port)
    socket.getaddrinfo = _guarded_getaddrinfo


def validate_url(url: str, allow_private: bool = False,
                 allowed_hosts: Iterable[str] = ()) -> Optional[str]:
    """
    URL অনুমোদিত না হলে বাংলায় error মেসেজ, নাহলে None।

    `://` ছাড়া ইনপুট (বেয়ার ভিডিও ID, `ytsearch:`, extractor prefix) অপরিবর্তিত অনুমোদিত —
    যাতে yt-dlp-র নিজস্ব পথগুলো কাজ করে।
    """
    if not isinstance(url, str):
        return "URL সঠিক নয়"

    candidate = url.strip()
    if "://" not in candidate:
        return None

    parts = urlsplit(candidate)
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return 'স্কিম "%s" অনুমোদিত নয় (শুধু http/https)' % parts.scheme

    hostname = parts.hostname
    if not hostname:
        return "URL-এ হোস্ট নেই"

    if allow_private:
        return None

    if _hostname_is_blocked(hostname):
        return 'ইন্টারনাল হোস্ট "%s" ফেচ করা হবে না' % hostname

    allowlist = {h.strip().rstrip(".").lower() for h in allowed_hosts if h and h.strip()}
    if allowlist:
        host = hostname.rstrip(".").lower()
        if not any(host == allowed or host.endswith("." + allowed) for allowed in allowlist):
            return 'হোস্ট "%s" allowlist-এ নেই (ALLOWED_HOSTS)' % hostname

    try:
        addrinfo = socket.getaddrinfo(hostname, parts.port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # fail closed: resolve না হলে যাচাই করা যায় না, তাই প্রত্যাখ্যান
        return 'হোস্ট "%s" resolve করা যায়নি' % hostname
    except (UnicodeError, ValueError):
        return 'হোস্ট "%s" অবৈধ' % hostname

    for _family, _type, _proto, _canon, sockaddr in addrinfo:
        addr = sockaddr[0]
        if not address_is_global(addr):
            return 'হোস্ট "%s" ইন্টারনাল ঠিকানায় যায় ("%s") — প্রত্যাখ্যাত' % (hostname, addr)

    return None


def guard_report(url: str, allow_private: bool = False,
                 allowed_hosts: Iterable[str] = ()) -> dict:
    """ডায়াগনস্টিক আউটপুট (UI/লগে দেখানোর জন্য)।"""
    error = validate_url(url, allow_private=allow_private, allowed_hosts=allowed_hosts)
    return {"url": url, "allowed": error is None, "error": error,
            "allow_private": allow_private}
