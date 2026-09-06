#!/usr/bin/env python3
"""
Automated UI and Integration Test for Staff Attendance Portal.
Executes headless Firefox Selenium tests against the running application.
"""

import os
import sys
import time
import json
import re
import urllib.request
import urllib.error

# Ensure clean headless environment (prevent X11 / XWayland permission conflicts)
for env_var in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY"):
    os.environ.pop(env_var, None)

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


BASE_URL = os.environ.get("APP_URL", "http://127.0.0.1:5000").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin@123")


def wait_for_server(url, timeout=30):
    """Wait for the web server to respond before starting UI tests."""
    print(f"[INFO] Checking if application is up at {url}...")
    start_time = time.time()
    status_url = f"{url}/api/status"
    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request(status_url, headers={"User-Agent": "AutomatedTest/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status in (200, 302):
                    try:
                        data = json.loads(resp.read().decode())
                        print(f"[PASS] Server is healthy! Status: {data.get('system_status')}")
                    except Exception:
                        print(f"[PASS] Web server is responding! HTTP {resp.status}")
                    return True
        except urllib.error.HTTPError as e:
            if e.code in (200, 302):
                print(f"[PASS] Web server responded with HTTP {e.code}")
                return True
        except Exception:
            pass

        # Fallback check on login / setup endpoint
        try:
            req = urllib.request.Request(f"{url}/login", headers={"User-Agent": "AutomatedTest/1.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status in (200, 302):
                    print(f"[PASS] Web server responded on login/setup endpoint! HTTP {resp.status}")
                    return True
        except urllib.error.HTTPError as e:
            if e.code in (200, 302):
                print(f"[PASS] Web server responded on login endpoint with HTTP {e.code}")
                return True
        except Exception:
            pass

        time.sleep(1)
    print(f"[ERROR] Server did not become ready within {timeout} seconds.")
    return False


def setup_driver():
    """Configure and return a headless Firefox WebDriver instance."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,800")
    driver = webdriver.Firefox(options=options)
    driver.implicitly_wait(5)
    return driver


def test_login_flow():
    """Run automated UI test suite."""
    if not wait_for_server(BASE_URL):
        sys.exit(1)

    driver = None
    try:
        print("[INFO] Initializing Headless Firefox WebDriver...")
        driver = setup_driver()
        wait = WebDriverWait(driver, 10)

        # -------------------------------------------------------------
        # 1. Load Login Page
        # -------------------------------------------------------------
        login_url = f"{BASE_URL}/login"
        print(f"[INFO] Navigating to: {login_url}")
        driver.get(login_url)

        # If redirected to /setup on first run, handle gracefully
        if "/setup" in driver.current_url:
            print("[INFO] Application is on /setup wizard page.")
            assert "Setup" in driver.title or "setup" in driver.current_url
            print("[PASS] Setup page loaded successfully.")
            return

        wait.until(EC.presence_of_element_located((By.NAME, "username")))
        print(f"[PASS] Login page loaded. Title: '{driver.title}'")

        # -------------------------------------------------------------
        # 2. Test Tab Switcher (Staff <-> Admin)
        # -------------------------------------------------------------
        print("[INFO] Testing login panel toggle switches...")
        admin_toggle = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-panel="admin"]')))
        admin_toggle.click()
        time.sleep(0.5)

        admin_panel = driver.find_element(By.ID, "admin")
        assert "active" in admin_panel.get_attribute("class"), "Admin panel should be active after click"
        print("[PASS] Admin login panel activated successfully.")

        staff_toggle = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-panel="staff"]')))
        staff_toggle.click()
        time.sleep(0.5)

        staff_panel = driver.find_element(By.ID, "staff")
        assert "active" in staff_panel.get_attribute("class"), "Staff panel should be active after click"
        print("[PASS] Staff login panel activated successfully.")

        # -------------------------------------------------------------
        # 3. Test Invalid Login Handling (Negative Test)
        # -------------------------------------------------------------
        print("[INFO] Testing invalid credentials handling...")
        username_input = staff_panel.find_element(By.NAME, "username")
        password_input = staff_panel.find_element(By.NAME, "password")
        submit_btn = staff_panel.find_element(By.CSS_SELECTOR, "button[type='submit']")

        username_input.clear()
        username_input.send_keys("invalid_automated_test_user")
        password_input.clear()
        password_input.send_keys("wrong_password_123")
        submit_btn.click()

        # Wait for page to reload / show error (flash alert or toast or page text)
        time.sleep(1)
        page_text = driver.page_source
        has_error = (
            "Invalid" in page_text or 
            "locked" in page_text or 
            "alert" in page_text or
            len(driver.find_elements(By.CSS_SELECTOR, ".alert, .flash-alert, .notif-item")) > 0
        )
        assert has_error, "Expected error notification for invalid login"
        print("[PASS] Negative login test verified: error was correctly triggered.")

        # -------------------------------------------------------------
        # 4. Test Valid Admin Login & 2FA PIN Flow (Positive Test)
        # -------------------------------------------------------------
        print(f"[INFO] Testing Admin Login for user '{ADMIN_USER}'...")
        driver.get(login_url)
        admin_toggle = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-panel="admin"]')))
        admin_toggle.click()
        time.sleep(0.5)

        admin_panel = driver.find_element(By.ID, "admin")
        admin_user_input = admin_panel.find_element(By.NAME, "username")
        admin_pass_input = admin_panel.find_element(By.NAME, "password")
        admin_submit = admin_panel.find_element(By.CSS_SELECTOR, "button[type='submit']")

        admin_user_input.clear()
        admin_user_input.send_keys(ADMIN_USER)
        admin_pass_input.clear()
        admin_pass_input.send_keys(ADMIN_PASS)
        admin_submit.click()

        time.sleep(1.5)
        # Check if PIN verification screen is active
        is_pin_screen = (
            len(driver.find_elements(By.NAME, "pin")) > 0 or
            "Verify PIN" in driver.page_source or
            "verify-pin" in driver.current_url
        )

        if is_pin_screen:
            print("[INFO] Reached 2FA PIN verification screen.")
            pin = None
            pin_elems = driver.find_elements(By.CLASS_NAME, "pin-code")
            if pin_elems and pin_elems[0].text.strip().isdigit():
                pin = pin_elems[0].text.strip()

            if not pin:
                try:
                    your_pin_lbl = driver.find_element(By.XPATH, "//*[contains(text(), 'Your PIN')]")
                    container = your_pin_lbl.find_element(By.XPATH, "..")
                    for child in container.find_elements(By.XPATH, "./*"):
                        txt = child.text.strip().replace(" ", "")
                        if len(txt) == 4 and txt.isdigit():
                            pin = txt
                            break
                except Exception:
                    pass

            if not pin:
                matches = re.findall(r'\b\d{4}\b', driver.page_source)
                for m in matches:
                    if m not in ("2024", "2025", "2026", "0000"):
                        pin = m
                        break

            print(f"[INFO] Detected 2FA PIN: {pin}")
            assert pin and len(pin) == 4 and pin.isdigit(), f"Failed to extract valid 4-digit PIN, got: {pin}"

            pin_input = wait.until(EC.presence_of_element_located((By.NAME, "pin")))
            pin_input.clear()
            pin_input.send_keys(pin)

            time.sleep(0.5)
            verify_btns = driver.find_elements(By.CSS_SELECTOR, "form[action*='verify-pin'] button[type='submit'], button[type='submit']")
            if verify_btns and "/admin" not in driver.current_url:
                try:
                    verify_btns[0].click()
                except Exception:
                    pass

            time.sleep(1.5)

        # Check if redirected to /admin or dashboard
        print(f"[INFO] Current page after login: {driver.current_url}")
        assert "/admin" in driver.current_url or "Admin" in driver.title or "/staff" in driver.current_url, (
            f"Expected successful login redirect, got URL: {driver.current_url}"
        )
        print(f"[PASS] Authentication successful! Current page: {driver.current_url}")

        # -------------------------------------------------------------
        # 5. Test Logout
        # -------------------------------------------------------------
        print("[INFO] Testing Logout flow...")
        driver.get(f"{BASE_URL}/logout")
        time.sleep(1)
        wait.until(EC.presence_of_element_located((By.NAME, "username")))
        assert "/login" in driver.current_url
        print("[PASS] Successfully logged out and returned to login page.")

        print("\n=======================================================")
        print("✅ ALL AUTOMATED UI & AUTHENTICATION TESTS PASSED!")
        print("=======================================================\n")

    except Exception as exc:
        print(f"\n❌ [FAIL] Test encountered an error: {exc}")
        if driver:
            try:
                screenshot_path = "test_failure_screenshot.png"
                driver.save_screenshot(screenshot_path)
                print(f"[INFO] Saved failure screenshot to: {screenshot_path}")
            except Exception:
                pass
        sys.exit(1)
    finally:
        if driver:
            driver.quit()
            print("[INFO] WebDriver stopped cleanly.")


if __name__ == "__main__":
    test_login_flow()
