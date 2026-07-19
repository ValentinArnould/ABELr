
## ABELr
Automated Batch Editing for Lightroom
This project is an Adobe Lightroom Classic Plugin made to help batch editing pain.

## Back story
I've started photography in August 2024, shooting at convention with cosplayers.
I quickly started to shoot everything and everyone with the mighty power of my Sony camera.
Everyone looked so enlighted by the pictures I made I couldn't stop.

Now it's July 2026, I shoot likely 1500 photos per event (almost no burst, always single pics), and end up with roughtly 5000 photos per month.
As much as I love giving the best result, I can't do it anymore (I did in the past, spending 50h in 10 days instead of sleeping, over and over).

This project is the fifth (sitxh ?) iteration of trying the make Adobe Lightroom better for my needs.
I've considered Darktable, ART rawtherapee, even doing my own RAW Editor, but Lightroom as it's signature process making hard to leave it.
I've spent months trying to do something, battle with Github Copilot and now Claude Code (yes, we'll speak about that later).
And this might be the closest project to succeed the task.

## The project
The project consist of :
- A plugin "Lr_Automation" serving as a bridge between the Python core and every functionnalities, being and API and likely an MCP for Claude.
- A python server with a GUI and simple buttons like "Test", "Analyse Catalog", "Mark references", "Apply"
- A whole processing and calculation part I don't know at all because despite being a developer,
    I don't know anything about GPU Image processing, Luminance median, Gray world nor HSL calculations, so I let AI do it's magic while trying to supervise the work

The plugin folder contain all the python application, you just need to add the folder in File->External Module Manager.
To launch the app server, in the Plugin Manager, click on "Démarrer l'Application" (sorry I'm French I haven't translated the project yet)

Be sure to install Python 3.1x and the virtual env needed (I just don't know why pytorch takes 4GB that's insane).
I use GPU processing because CPU usage just froze my PC to death during image analysis, I'm trying to maintain the CPU fallback, feel free to help if you need it.

## Functionalities and how it's works
Basic Context : Open Adobe Lightroom Classic, Open or Start a Catalog, get your photo displayed.
Let's say you have 1000 photos (my daily punishment), apply the preset you want on your photos (the signature look of your set)
Start ABELr, the plugin will maintain communication on the given port, and the app will request it for every action.

Start with "Analyse Catalog", it will create a db file in the active catalog folder. This db file will contain everything needed for analysis and calculation.
Edit a group of photos the way you want them (tone curve, color grading, effect, corrections, masking)
Select them and start "Mark references"


## Known limitations
This project was (very) personal, and I'm sharing it with you thinking it might serve a much bigger purpose, and help many other people struggling with the same problem.
So I'm asking for help, I would like help to make the best tool it can be, and solve the things I can't do alone.

Actual context limitations :
- I'm French, so the whole project (documentation, Claude files, everything except code) is in French. I'll gladly pass the project in English if you translate it right.
- I'm on Windows 11, scripts are in powershell and other python packages run for Windows. If you have a Mac or Linux, feel free to add support for your hardware.
- I use a Sony A7 IV Camera, exif data and raw processing works for .ARW files. It can be tricky on some part, but I'm kindly asking for other camera support.
- The app isn't finished, some functionnalities doesn't completely give the wanted result. I can battle Claude all I want, I still miss Photography knowledge for this.