# Multi-File Project Demo Script

Use the command input box or voice control. After each generated project, CodeUp should announce the active file and the project file list.

## Beginner Quiz Game

1. `create a quiz game split into multiple files`
2. `read project files`
3. `open questions dot py`
4. `open score dot py`
5. `open main dot py`
6. `explain project structure`
7. `run main dot py`

Expected output includes `Quiz game starting` and `Final score: 3 out of 3`.

## Pandas Marks Analysis

1. `make a student marks analysis project using pandas`
2. `read project files`
3. `open data slash marks dot csv`
4. `open data loader dot py`
5. `explain requirements`
6. `open main dot py`
7. `run main dot py`

Expected output includes `Student marks summary`, `Average mark`, and `Top student` when pandas is installed. If pandas is missing, CodeUp should say that the missing dependency is `pandas` and point to `requirements.txt`.

## Numpy Statistics Project

1. `make a numpy statistics project with tests`
2. `read project files`
3. `open stats utils dot py`
4. `open tests slash test main dot py`
5. `run tests slash test main dot py`
6. `open main dot py`
7. `run main dot py`

Expected output includes `Tests passed` for the test file and `Numpy statistics project` for `main.py` when numpy is installed.

## File Navigation And Editing

1. `create file notes dot md`
2. Type a short note in the editor.
3. `read project files`
4. `rename this file to project notes dot md`
5. `read project files`
6. `delete this file`
7. `read project files`

Each file change should produce a concise spoken status message.

## Missing Dependency Explanation

1. `make a student marks analysis project using pandas`
2. `open main dot py`
3. If pandas is not installed in the environment, run `run main dot py`.

Expected result when pandas is missing: the run fails safely with a message that names `pandas`, says it is missing, and points to `requirements.txt`.

## Return To Single-File Demo Flow

1. Load any normal demo or snippet, or clear the editor.
2. Type `print("Hello CodeUp!")`.
3. Press `Ctrl+Enter` or say `run`.

Expected output: `Hello CodeUp!`.
