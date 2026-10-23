"""Small HTTP helpers (standard library only)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

USER_AGENT = "moza-fcd-everyday/1.0 (personal desk display)"


def get_json(url: str, params: dict = None, timeout: float = 10.0):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body.decode("utf-8")) if body.strip() else None


def post_form(url: str, fields: dict, timeout: float = 10.0):
    data = urllib.parse.urlencode(fields).encode("ascii")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                                          "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
