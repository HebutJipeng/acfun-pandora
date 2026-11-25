"""Sign in to AcFun by scanning a QR code; no browser is launched."""

import argparse
import base64
import json
import os
import time

import requests

from acfun_client import ROOT, SESSION_FILE, AcFunClient

QR_BASE = "https://scan.acfun.cn/rest/pc-direct/qr/"
QR_IMAGE = ROOT / "data" / "acfun-login-qr.png"


def get_json(session, path, **params):
    response = session.get(QR_BASE + path, params=params, timeout=130, allow_redirects=False)
    if response.status_code != 200:
        raise RuntimeError(f"AcFun 扫码接口返回 HTTP {response.status_code}")
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("AcFun 扫码接口返回了意外的数据")
    return result


def login():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 AcFunPublisher/1.0", "Referer": "https://www.acfun.cn/login/"})
    start = get_json(session, "start", type="WEB_LOGIN")
    if start.get("result") != 0:
        raise RuntimeError(f"AcFun 无法生成登录二维码：{start.get('result')}")
    QR_IMAGE.parent.mkdir(parents=True, exist_ok=True)
    QR_IMAGE.write_bytes(base64.b64decode(start["imageData"]))
    try:
        print(f"请用 AcFun 手机 App 扫描并确认：{QR_IMAGE}", flush=True)
        deadline = time.monotonic() + min(int(start.get("expireTime", 120000)) / 1000, 120)
        token, signature = start["qrLoginToken"], start["qrLoginSignature"]
        while time.monotonic() < deadline:
            scanned = get_json(session, "scanResult", qrLoginToken=token, qrLoginSignature=signature)
            if scanned.get("result") == 0:
                break
            if scanned.get("result") == 100400002:
                raise RuntimeError("AcFun 登录二维码已过期，请重新运行 login")
            time.sleep(2)
        else:
            raise RuntimeError("等待扫码超时，请重新运行 login")
        accepted = get_json(session, "acceptResult", qrLoginToken=token,
                            qrLoginSignature=scanned["qrLoginSignature"])
        if accepted.get("result") != 0:
            raise RuntimeError(f"AcFun 扫码确认失败：{accepted.get('result')}")
        cookies = session.cookies.get_dict(domain=".acfun.cn")
        if not {"acPasstoken", "auth_key"} <= cookies.keys():
            raise RuntimeError("AcFun 已确认扫码，但未返回完整会话 Cookie")
        cookie = "; ".join(f"{key}={value}" for key, value in cookies.items())
        AcFunClient(cookie).post("/common/api/getPersonalInfo", json_data={}, referer="/")
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(SESSION_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"cookie": cookie}, stream)
            stream.write("\n")
        os.chmod(SESSION_FILE, 0o600)
    finally:
        QR_IMAGE.unlink(missing_ok=True)
    print("AcFun 登录成功，会话已保存至 data/acfun-session.json", flush=True)


def status():
    result = AcFunClient().post("/common/api/getPersonalInfo", json_data={}, referer="/")
    print(json.dumps({"logged_in": bool(result.get("userId") or result.get("info")),
                      "user_id": result.get("userId") or (result.get("info") or {}).get("userId")}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["login", "status"])
    args = parser.parse_args()
    login() if args.command == "login" else status()


if __name__ == "__main__":
    main()
