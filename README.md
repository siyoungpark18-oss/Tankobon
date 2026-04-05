# Tankobon. #

Tankobon is a manga file manager. 
It is Free, Open Source, Cross-platform, and Local that is made for personal computers. 

*Its sole purpose of existence is to make manga piracy easier.*

Rather than being a third party manga downloader, Tankobon is dedicated to Manga piracy & the file management around it. 
There is some setup involved. See **Setup**
<br>
<br>

## To Open Tankobon ##
On Mac, use the resource here: *https://drive.google.com/drive/u/2/folders/1DDJeCJzbLldOlOj6I0NC7C-7LKknRsL2* to download a .dmg file and open it like any other application. 
You will have to go through privacy and security sections to give MacOS permission to open it as I am not a registered developer

On Windows use the resource here: *https://drive.google.com/drive/u/2/folders/1CJa1_Xs59f6V0MoZDcGeH6AjkI2S0Jh6* to download the zip file. Expand it and execute the .exe file to use the application.
<br>
<br>

## Why Use Tankobon? ##

*Tankobon’s Advantage lies in sheer speed and scale.* 
It can convert, re-organize, and combine **thousands of files in a matter of minutes to seconds. This is orders of magnitude faster than most browser based tools or applications.**
-   *Tankobon does this by utilizing your local file system rather than requiring the user to upload files to an app or cloud. This is what allows the app to be entirely local.*


**Fear not! Tankbon does not modify you’re original files. The input and output folders all work off copies of each other or your original files, preventing any irreversible changes to your files.** 

This means that Clearing the input and output will never permanently delete your original files. Thus, there is no undo button. 

If you don’t like the output just redo it or remove it. Some features that make this easier are:
- *open most recent output*—allows you to open the most recent output with open output, rather than the entire output folder. 
- *replace output*—replaces old outputs with the most recent one. If you're current output is faulty, simply replace it with a new one with this setting.
<br>

## Setup ##
When you first begin the program, **please set the directories for the Input and Output within your file system and give the application permissions to read/write files there for actual functionality**

### Poppler ###
Poppler is not packaged in as of yet. Please install it yourself. The program should work overall even without it, but some tools will simply break. Poppler is a python dependency. some PDF functions will not work without it.
<br>
<br>

## Controls ##
**Main Bar**

☀ to switch between light and dark mode

? for Help

i for documentation and resources

≡ for Preferences

**Log**
to Increase the size of Text in Log, hit +

to Decrease the size of Text in Log, hit -

Use the Scrollbar to Scroll in the Log, and hit **Clear Log** to clear all of its contents
<br>

## File System ##
***The file system is organized into an Input and Output folder*** 
The Input folder is where files are moved to for processing with the tools. *Files from the Input folder are copied from your file system*
Do this using the **Add to Input** Button

The Output folder is where these files go to when they are processed. *Files from the Output folder are copied from the Input folder with their modifications*
To export the processed files and move them back into your file system, use the **Open Output** Button
<br>
<br>

## Intended Workflow ##
For Tankobon, there is a rather clear cut intentional workflow that should be followed to use the program smoothly

1. Add Files to the Input folder with the **Add to Input** tool
2. Use the **File and Folder Tools** to process the input. *The input is not modified in the proccess, so multiple processes can be done on with same input*
3. Export the Output files and move them back into your file system using **Open Output**
4. Once you are done with the current input, clear the input with **Clear Input**

Once you are done, add new files with **Add to Input** and continue the process. You can even add the current output with **From Output**
<br>
<br>

## Input Controls ##
Controls for the Input Folder

***Add Input:*** Add files or **one folder**

***Clear Input:*** Clears the current input of all of its contents

***Open Input:*** Opens the input folder. Use this to check what specific files are in input
<br>
<br>

## Utilities ##
Other tools
***Status:*** States whats in the input and output. Using the ▲ and ▼ buttons, you can expand the log dialogue to see what subfolders are inside. 
*For conciseness, Status and other expandable tools only show subfolders and not individual files*

***Clear Log:*** Clears the Log and all of its contents, including any expandable dialogue ( ▲ ▼ )

***Open Output:*** Opens the Output folder. Use this tool as a shortcut to open the output folder and move files in it to your main file system. 

***Clear Output:*** Clears the Output folder and all of its contents

***Cancel Operation:*** Cancels the operation. For some operations this takes some time. 
Cancel checks happen periodically so that the operations are held back.
<br>
<br>

## Limitations ##
for all its speed, it has some pretty quirky limitations.

- **Only 1 folder can be attached at a time!** *If you want to process folders at a time, move all of the folders into 1 main folder beforehand and attach that main folder.*
If you have multiple folders of chapters you want to modify, simply put all chapters in one main folderand then you can attach that folder with its subfolders all at once. This is a known limitation and will eventually be changed

- In all processes the tool is capable of, **you cannot pick the files within the input that are to be processed.** It will process all of the files in the input automatically, and this is intentional. **To avoid processing unrelated folders or files, you should clear the input folder periodically when you’re done with the current files** in input and then add newer ones left to process. This is the intended workflow and prevents the program from having to constantly ask you what to process.

Everything in the input is simply processed in its entirity. And it is meant to be this way. Reference the Intended workflows to see how the workflow functions with it.
<br>
<br>

## Throttle ##
Limits to CPU and RAM Usage

*Under certain circumstances, Tankobon can easily cause your computer to freeze due to taking up all the ram and CPU on your computer.* **There is a throttle set by default that limits how much cpu and ram Tankobon can use to prevent this. The default is 80%**

***If your comptuer is already using CPU or RAM above te throttle set, the tools will immediately pause, and appear as if they have not started!***
