# Changelog

## v1.3.3vs4

Make sure that every time this is run by the extension everyone knows this is a contribution by fireundubh since it's being bundled and it's not entirely obvious. Also I'm changing the version numbering for the fork to use firundubh's version (v1.3.3 etc) plus vs followed by a sequence number. This way you'll be able to tell what version of the master repo it is based on, but will know it's a fork for vscode and something about how it differs.

## v1.3.3n3

Add option to disable parallelization in case it's needed for debugging. Also add some description to the help and make -c optional since it is.

## v1.3.3n2

Needed to add the multiprocessing plugin so that multiprocessing will work. Now it works! But dependency checking isn't working so everything is rebuilt every time.

## v1.3.3n1

Successfully build with Nuitka, but the result is totally broken. Don't use it. Multiprocessing doens't work in this one so it just barfs on compiling.