package com.matchstick;

import android.os.SystemClock;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.KeyCharacterMap;
import android.view.KeyEvent;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.reflect.Method;

/**
 * Matchstick key helper: runs on the Fire TV (app_process, as the adb shell user) and injects
 * key presses the same way Android's `input keyevent` does, but stays running so each press
 * costs milliseconds instead of a Java start-up.
 *
 * Reads lines on stdin, answers one line per request:
 *   k <keycode> [keycode ...]   press each key            -> ok | fail
 *   l <keycode>                 long-press the key         -> ok | fail
 * Prints "ready" once it can inject.
 */
public class Keys {
    private static Object manager;
    private static Method inject;

    public static void main(String[] args) throws Exception {
        // InputManager.getInstance() and injectInputEvent() are hidden APIs: reach them by reflection.
        Class<?> im = Class.forName("android.hardware.input.InputManager");
        manager = im.getMethod("getInstance").invoke(null);
        inject = im.getMethod("injectInputEvent", InputEvent.class, int.class);
        System.out.println("ready");
        System.out.flush();

        BufferedReader in = new BufferedReader(new InputStreamReader(System.in));
        String line;
        while ((line = in.readLine()) != null) {
            String[] p = line.trim().split("\\s+");
            if (p.length < 2) {
                continue;
            }
            boolean ok = true;
            try {
                boolean longPress = p[0].equals("l");
                for (int i = 1; i < p.length; i++) {
                    ok &= press(Integer.parseInt(p[i]), longPress);
                }
            } catch (Exception e) {
                ok = false;
            }
            System.out.println(ok ? "ok" : "fail");
            System.out.flush();
        }
    }

    // Same events as `input keyevent [--longpress]` (com.android.commands.input.Input).
    private static boolean press(int code, boolean longPress) throws Exception {
        long now = SystemClock.uptimeMillis();
        boolean ok = send(new KeyEvent(now, now, KeyEvent.ACTION_DOWN, code, 0, 0,
                KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD));
        if (longPress) {
            ok &= send(new KeyEvent(now, now, KeyEvent.ACTION_DOWN, code, 1, 0,
                    KeyCharacterMap.VIRTUAL_KEYBOARD, 0, KeyEvent.FLAG_LONG_PRESS, InputDevice.SOURCE_KEYBOARD));
        }
        ok &= send(new KeyEvent(now, now, KeyEvent.ACTION_UP, code, 0, 0,
                KeyCharacterMap.VIRTUAL_KEYBOARD, 0, 0, InputDevice.SOURCE_KEYBOARD));
        return ok;
    }

    private static boolean send(KeyEvent e) throws Exception {
        // 2 = INJECT_INPUT_EVENT_MODE_WAIT_FOR_FINISH, as `input` uses.
        return (Boolean) inject.invoke(manager, e, 2);
    }
}
