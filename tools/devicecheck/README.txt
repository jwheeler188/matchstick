MATCHSTICK DEVICE CHECK
=======================

Thanks for helping test. Matchstick lets a Savant home-control system work an Amazon Fire TV
over the network. This kit tries it on your device (it was built for Fire TV, so some things may
not work) and saves a report showing what does and doesn't.

You run one command. It sends a command to the device, tells you what should happen on the TV,
checks what the device itself reports, and asks whether it worked. Where there's a plain
Android way to do the same thing (keys, search, typing, Enter, power), it tries that too.
It takes about 20 minutes.


SCREENSHOTS: PLEASE READ
------------------------
The check takes a screenshot of the device's screen after each step, about 30 in all. Whatever
the TV is showing at that moment ends up in the report: the home screen with its
recommendations, your apps, search results, and the profile or account name if an app shows it.

They're saved here, inside this kit's folder, before anything is sent:

    report-<date-time>/steps/          for example  report-20261030-1915/steps/

Look through them before you send the report back, and delete any you'd rather not share. The
script prints the exact folder and the command to rebuild the file after deleting any.

To take no screenshots at all, add --no-screenshots (see step 2). The check still works; it
just can't show what the screen looked like.


WHAT YOU NEED
-------------
- A Linux (or Mac) computer on the same network as the device, with Python 3.7 or newer
  (check: python3 --version) and internet access (it downloads Google's adb tool once).
- The TV on, showing the device, and the device's own remote nearby.


1. TURN ON NETWORK DEBUGGING ON THE DEVICE
------------------------------------------
NVIDIA Shield:
  a. Settings > Device Preferences > About. Select "Build" 7 times, until it says you're a
     developer.
  b. Back to Device Preferences > Developer options. Turn on "Network debugging".
  c. Note the device's IP address: Settings > Device Preferences > About > Status
     (or Settings > Network & Internet > your network).


2. RUN THE CHECK
----------------
Unpack this folder, open a terminal in it, and run one of these (with the device's IP):

    bash run.sh 192.168.1.50                      you watch the TV and answer after each step
    bash run.sh 192.168.1.50 --unattended         runs by itself; no questions
    bash run.sh 192.168.1.50 --no-screenshots     either way, without screenshots

- The first time, the TV asks to allow debugging from this computer: tick "Always allow" and
  choose OK. That's the only thing unattended mode needs from you.
- Watching and answering is more useful: answer y (yes, it did that), n (no) or s (skip / not
  sure). If you say no, type a few words about what happened instead. Some things only you can
  see, such as whether the TV's volume changed.
- One step asks you to start a video with your own remote first. Unattended, it starts a
  YouTube video itself (if YouTube is installed).
- Ctrl-C stops at any time. The report so far is still saved.


3. CHECK THE SCREENSHOTS, THEN SEND THE RESULT BACK
---------------------------------------------------
At the end it prints the screenshot folder and the name of one file, like:

    Screenshots: .../report-20261030-1915/steps/
    Send this file back: .../matchstick-check-SHIELD-Android-TV-20261030-1915.tar.gz

Look through the screenshots first (see SCREENSHOTS above), then send that file back. If you
delete any screenshots, run the tar command the script prints so the file leaves them out.


WHAT'S IN THE REPORT
--------------------
- The device's model, Android version and build, its list of TV apps, and technical details of
  how its screen, keyboard, power and media playback report their state.
- What Matchstick sent and reported back, an automatic pass/fail for each step where the device
  can tell, and your answers.
- The screenshots described above (unless you used --no-screenshots).

It doesn't collect accounts, passwords, Wi-Fi names or serial numbers.


WHAT IT CHANGES
---------------
- On this computer: nothing is installed. It downloads adb into this folder and runs its own
  copy (on port 5038), so any adb you already use is left alone.
- On the device: a small key helper is copied to a temporary folder for the test and removed
  at the end. It types a few test searches (stargate, matrix, star trek, dune), which may show
  in the device's recent searches. Nothing else is installed or changed.
- Afterwards, turn Network debugging off again (Developer options) if you like.
