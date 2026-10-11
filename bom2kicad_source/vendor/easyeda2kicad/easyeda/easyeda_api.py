# Global imports
import logging
import random
import time

import requests

from easyeda2kicad import __version__

API_ENDPOINT = "https://easyeda.com/api/products/{lcsc_id}/components?version=6.4.19.5"
ENDPOINT_3D_MODEL = "https://modules.easyeda.com/3dmodel/{uuid}"
ENDPOINT_3D_MODEL_STEP = "https://modules.easyeda.com/qAxj6KHrDKw4blvCG8QJPs7Y/{uuid}"
# ENDPOINT_3D_MODEL_STEP found in https://modules.lceda.cn/smt-gl-engine/0.8.22.6032922c/smt-gl-engine.js : points to the bucket containing the step files.

# ------------------------------------------------------------


def _http_get(url: str, headers: dict, timeout: float):
    """GET with a shared session and retry-on-connection-reset."""
    global _SESSION
    try:
        return _SESSION.get(url=url, headers=headers, timeout=timeout)
    except Exception:
        # connection reset / stale pooled connection -> fresh session, one retry
        _new_session()
        return _SESSION.get(url=url, headers=headers, timeout=timeout)


_SESSION = None


def _new_session():
    global _SESSION
    _SESSION = requests.Session()
    return _SESSION


_new_session()


# sleep hook lets the host application (KiCad GUI) slice these backoff
# waits so its event loop stays alive; defaults to plain blocking sleep
_SLEEP_HOOK = None
_CANCEL_CHECK = None

# diagnostics surfaced to the host: 'ok' | 'HTTP <code>' | ...
_LAST_ERROR_REASON = "ok"
_last_status_code = None


def last_error_reason() -> str:
    return _LAST_ERROR_REASON


def set_last_error_reason(reason: str) -> None:
    global _LAST_ERROR_REASON
    _LAST_ERROR_REASON = reason


def set_sleep_hook(fn) -> None:
    global _SLEEP_HOOK
    _SLEEP_HOOK = fn


def set_cancel_check(fn) -> None:
    """fn() -> True while the host wants to abort the current import."""
    global _CANCEL_CHECK
    _CANCEL_CHECK = fn


def _perform_sleep(seconds: float) -> bool:
    """Interruptible backoff sleep. Returns False when cancelled."""
    if _SLEEP_HOOK is not None:
        try:
            result = _SLEEP_HOOK(seconds)
        except Exception:
            return True
        return result is not False
    end = time.time() + max(seconds, 0.0)
    while True:
        if _CANCEL_CHECK is not None:
            try:
                if _CANCEL_CHECK():
                    return False
            except Exception:
                pass
        now = time.time()
        if now >= end:
            return True
        time.sleep(min(0.25, end - now))


class EasyedaApi:
    def __init__(self) -> None:
        self.headers = {
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

    def get_info_from_easyeda_api(self, lcsc_id: str) -> dict:
        global _LAST_ERROR_REASON, _last_status_code
        api_response = None
        last_exception = None
        _LAST_ERROR_REASON = "ok"
        _last_status_code = None
        # stay under the ~15 req/min budget; 403 blocks last ~2 minutes, so a
        # patient-but-bounded ladder [0.5, 3, 20, 40, 60] (~2 min) usually
        # outlives one block; after that fail fast (the host pauses once and
        # reschedules) instead of crawling for minutes per part
        for delay in [0.5, 3, 20, 40, 60]:
            if _CANCEL_CHECK is not None:
                try:
                    if _CANCEL_CHECK():
                        logging.warning(
                            f"EasyEDA import cancelled during backoff ({lcsc_id})"
                        )
                        return {}
                except Exception:
                    pass
            try:
                r = _http_get(
                    url=API_ENDPOINT.format(lcsc_id=lcsc_id),
                    headers=self.headers,
                    timeout=60,
                )
                _last_status_code = r.status_code
                if r.status_code == 200 and r.text.strip().startswith("{"):
                    api_response = r.json()
                    _LAST_ERROR_REASON = "ok"
                    break
                _LAST_ERROR_REASON = f"HTTP {r.status_code}"
                last_exception = RuntimeError(
                    f"HTTP {r.status_code} from EasyEDA API"
                )
                logging.warning(
                    f"EasyEDA API throttled ({r.status_code}), "
                    f"waiting {delay}s before retry {lcsc_id}"
                )
            except Exception as e:
                last_exception = e
                _LAST_ERROR_REASON = f"network error: {e}"[:120]
            if not _perform_sleep(delay + random.uniform(0, delay * 0.2)):
                logging.warning(f"EasyEDA import cancelled during backoff ({lcsc_id})")
                return {}
        else:
            logging.warning(
                f"EasyEDA API request failed for {lcsc_id} after retries: "
                f"{last_exception}"
            )
            return {}

        if not api_response or (
            "code" in api_response and api_response["success"] is False
        ):
            logging.debug(f"{api_response}")
            return {}

        return api_response

    def get_cad_data_of_component(self, lcsc_id: str) -> dict:
        cp_cad_info = self.get_info_from_easyeda_api(lcsc_id=lcsc_id)
        if cp_cad_info == {}:
            return {}
        return cp_cad_info["result"]

    def get_raw_3d_model_obj(self, uuid: str) -> str:
        r = _http_get(
            url=ENDPOINT_3D_MODEL.format(uuid=uuid),
            headers={"User-Agent": self.headers["User-Agent"]},
            timeout=60,
        )
        if r.status_code != requests.codes.ok:
            logging.error(f"No raw 3D model data found for uuid:{uuid} on easyeda")
            return None
        return r.content.decode()

    def get_step_3d_model(self, uuid: str) -> bytes:
        r = _http_get(
            url=ENDPOINT_3D_MODEL_STEP.format(uuid=uuid),
            headers={"User-Agent": self.headers["User-Agent"]},
            timeout=60,
        )
        if r.status_code != requests.codes.ok:
            logging.error(f"No step 3D model data found for uuid:{uuid} on easyeda")
            return None
        return r.content
