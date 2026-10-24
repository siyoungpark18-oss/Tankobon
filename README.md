# Tankobon. #

Tankobon is a Free & Open source manga file manager. It is meant as a post-processor for after you've obtained the files and before you host them locally on servers like Kavita. 
- It is not another implementation of Mangarr or any application that assists in actually pirating the file, there are plenty of those around.

*Its sole purpose of existence is to make file management with manga piracy easier.*
However, it can be used for cleaning and assimilating disorganized files broadly.

Tankobon is not a terminal tool. It operates under a simple GUI. If a terminal tool is what you're looking for, this is not what you're looking for.
While it supports Epubs and has basic Comicsinfo.XML support, it does not intend to be a specialized tool for Kindle or other E-readers. 


# Broad Use Case #
Tankobon is not particularly good at any one thing in particular. Its main strength is in its functionality as a pipeline tool. While other tools can do any individual job in Tankobon better, their functionalities can be combined into workflows.
- Combine tools together to make workflows!

Broadly in terms of individual tools it can combine, reorder, rename, separate, and convert, and dedeupe files, folders, and compressed formats.
- It can work with file types: zip, cbz, uncompressed folders, pdf and epub. As of yet, it does not work with .rar files, ect.

Its Primary Intended Utilities are:
- Assimilate folders into consistent naming conventions.
- Assimilate Image files into consistent file formats.
- Find duplicate images or Scanlation Advertisements — more on this later.
- Combine dozens of chapters into 1 simple file or volume.
- Append Metadata en Masse to groups of files.

More Specific Use Cases:
- Name the folders based on their chapter number—making it easier to keep track of files, organize, and manage them.
- Find duplicate images from repetitive scanlater advertisements, while keeping the memes. Can also be used to remove start pages or empty panels for webcomics.
- Combine all 200 chapters of berserk into 1 pdf. Because why not?

Local Libraries like Kavita that can handle naming inconsistencies and individual chapters make many of these tools perhaps irrelevent, but this tool is still good for maintaining consistent conventions and management in general.
- Upon making this tool, it seems to be of limited usefullness unless you have file OCD or something, but I made this tool when I didn't know Kavita existed and such, and read pdf files manually.


# Files #
All Files imported into the application are copied rather than moved and during operations. This prevents the initial imported file from being lossed, and prevents loosing the initial input if the output fails. 
- This does however increase disk usage.

All File Utilities operate losslessly in terms of file quality. 
- File size compression might be added later
