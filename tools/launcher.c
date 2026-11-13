/*
 * Desk Guard.app's executable. Runs the Python app inside this process, so macOS sees one
 * app called Desk Guard (Dock icon, name, camera permission) instead of "Python".
 *
 * It uses the project's .venv, found next to the .app or at the path baked in at build
 * time (PROJECT_DIR). Output goes to desk.log in the project folder.
 * Built by tools/make_app.sh.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#ifndef PROJECT_DIR
#error "build with -DPROJECT_DIR=\"/path/to/desk-guard\""
#endif
#ifndef APP_MODULE
#define APP_MODULE "webcam_fun.menubar"
#endif

static int is_project(const char *dir) {
    char path[PATH_MAX];
    struct stat st;
    snprintf(path, sizeof path, "%s/webcam_fun", dir);
    return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

/* The folder holding Desk Guard.app if it is a project checkout, else PROJECT_DIR. */
static void find_project(char *out, size_t size) {
    char exe[PATH_MAX], real[PATH_MAX];
    uint32_t len = sizeof exe;
    if (_NSGetExecutablePath(exe, &len) == 0 && realpath(exe, real) != NULL) {
        /* .../X/Desk Guard.app/Contents/MacOS/Desk Guard -> .../X */
        char *dir = real;
        for (int i = 0; i < 4; i++) dir = dirname(dir);
        if (is_project(dir)) {
            snprintf(out, size, "%s", dir);
            return;
        }
    }
    snprintf(out, size, "%s", PROJECT_DIR);
}

static void fail(const char *message) {
    char cmd[1024];
    fprintf(stderr, "Desk Guard: %s\n", message);
    snprintf(cmd, sizeof cmd,
             "osascript -e 'display alert \"Desk Guard could not start\" message \"%s\"'",
             message);
    system(cmd);
    exit(1);
}

int main(int argc, char **argv) {
    char project[PATH_MAX], python[PATH_MAX], log[PATH_MAX];
    (void)argc;
    find_project(project, sizeof project);
    snprintf(python, sizeof python, "%s/.venv/bin/python", project);
    if (access(python, X_OK) != 0)
        fail("It is not set up yet. In Terminal, in the desk-guard folder, run ./run desk once.");
    if (chdir(project) != 0) fail("Could not open the project folder.");

    /* Send output to desk.log. Each stream on its own: freopen closes the old stream
       first, so a failure must not leave the other one closed too. */
    snprintf(log, sizeof log, "%s/desk.log", project);
    FILE *out = fopen(log, "a");
    if (out != NULL) {
        fclose(out);  /* it can be opened: now point both streams at it */
        if (freopen(log, "a", stdout) != NULL) setvbuf(stdout, NULL, _IOLBF, 0);
        if (freopen(log, "a", stderr) != NULL) setvbuf(stderr, NULL, _IONBF, 0);
    }

    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
    /* Pointing executable at the venv's python makes Python find .venv/pyvenv.cfg and use
       the venv's site-packages, exactly as if the venv python had been started. */
    status = PyConfig_SetBytesString(&config, &config.executable, python);
    if (PyStatus_Exception(status)) goto error;
    status = PyConfig_SetBytesString(&config, &config.program_name, python);
    if (PyStatus_Exception(status)) goto error;
    char *args[] = {argv[0], "-u", "-m", APP_MODULE};
    status = PyConfig_SetBytesArgv(&config, 4, args);
    if (PyStatus_Exception(status)) goto error;
    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) goto error;
    PyConfig_Clear(&config);
    return Py_RunMain();

error:
    PyConfig_Clear(&config);
    if (PyStatus_IsExit(status)) return status.exitcode;
    Py_ExitStatusException(status);
}
