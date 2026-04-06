# Tankobon. #

Tankobon is a Free & Open source manga file manager. 

*Its sole purpose of existence is to make manga piracy easier.*

## Why Use Tankobon? ##

*Tankobon’s Advantage lies in sheer speed and scale.* 
It can convert, re-organize, and combine *thousands of files in a matter of minutes to seconds.
-   *Tankobon does this by utilizing your local file system rather than requiring the user to upload files to an app or cloud. This is what allows the app to be entirely local.*

## Download ##
Get the tool from the [releases page](https://github.com/siyoungpark18-oss/Tankobon/releases) on github.

## How it Works ##
Tankobon uses an Input and Output folder system to import and export files.

The Input folder is where files are moved to for processing with the tools. *Files from the Input folder are copied from your file system*
Do this using the **Add to Input** Button

The Output folder is where these files go to when they are processed. *Files from the Output folder are copied from the Input folder with their modifications*
To export the processed files and move them back into your file system, use the **Open Output** Button

For Tankobon, there is a rather clear cut intentional workflow that should be followed to use the program smoothly

1. Add Files to the Input folder with the **Add to Input** tool
2. Use the **File and Folder Tools** to process the input. *The input is not modified in the proccess, so multiple processes can be done on with same input*
3. Export the Output files and move them back into your file system using **Open Output**
4. Once you are done with the current input, clear the input with **Clear Input**

Once you are done, add new files with **Add to Input** and continue the process. You can even add the current output with **From Output**

## Utilities ##
***Status:*** States whats in the input and output. Using the ▲ and ▼ buttons, you can expand the log dialogue to see what subfolders are inside. 
***Clear Log:*** Clears the Log and all of its contents, including any expandable dialogue ( ▲ ▼ )
***Open Output:*** Opens the Output folder. Use this tool as a shortcut to open the output folder and move files in it to your main file system. 
***Clear Output:*** Clears the Output folder and all of its contents
***Cancel Operation:*** Cancels the operation.

## Limitations ##
for all its speed, it has some pretty quirky limitations.

- **Only 1 folder can be attached at a time!** *If you want to process folders at a time, move all of the folders into 1 main folder beforehand and attach that main folder.*
If you have multiple folders of chapters you want to modify, simply put all chapters in one main folderand then you can attach that folder with its subfolders all at once. This is a known limitation and will eventually be changed

- In all processes the tool is capable of, **you can't choose what files in the input input folder that you want processed.** Tankobon will process all of the files in the input, and this is intentional. Once you're done with the files in input, simply clear them with **clear input**.


## Throttle ##
Limits to CPU and RAM Usage

*Tankobon can easily cause your computer to freeze due to taking up all the ram and CPU on your computer.* **There is a throttle set by default that limits how much cpu and ram Tankobon can use to prevent this. The default is 80%**

***If your comptuer is already using CPU or RAM above te throttle set, the tools will immediately pause, and appear as if they have not started!***
