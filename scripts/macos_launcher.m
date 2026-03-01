#import <Foundation/Foundation.h>
#include <Python.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(int argc, char **argv) {
    @autoreleasepool {
        NSString *resources = NSBundle.mainBundle.resourcePath;
        NSDictionary *config = [NSDictionary dictionaryWithContentsOfFile:
            [resources stringByAppendingPathComponent:@"LaunchConfig.plist"]];
        if (!config) {
            fprintf(stderr, "Missing LaunchConfig.plist\n");
            return 1;
        }

        NSString *logDirectory = [NSHomeDirectory()
            stringByAppendingPathComponent:@"Library/Logs/Sidecue"];
        NSError *error = nil;
        if (![NSFileManager.defaultManager createDirectoryAtPath:logDirectory
                withIntermediateDirectories:YES attributes:nil error:&error]) {
            fprintf(stderr, "Cannot create log directory: %s\n", error.description.UTF8String);
            return 1;
        }
        NSString *logPath = [logDirectory stringByAppendingPathComponent:@"app.log"];
        if (!freopen(logPath.fileSystemRepresentation, "a", stdout) ||
            !freopen(logPath.fileSystemRepresentation, "a", stderr)) {
            return 1;
        }
        setvbuf(stdout, NULL, _IOLBF, 0);
        setvbuf(stderr, NULL, _IOLBF, 0);
        fprintf(stderr, "--- native launch %s ---\n", NSDate.date.description.UTF8String);

        NSString *appRoot = [resources stringByAppendingPathComponent:@"sidecue"];
        NSString *pythonPath = [NSString stringWithFormat:@"%@:%@",
            [resources stringByAppendingPathComponent:@"site-packages"], appRoot];
        NSString *path = [NSString stringWithFormat:@"%@:%s", config[@"CodexDirectory"],
            getenv("PATH") ?: "/usr/bin:/bin:/usr/sbin:/sbin"];
        setenv("PYTHONHOME", [config[@"PythonHome"] UTF8String], 1);
        setenv("PYTHONPATH", pythonPath.UTF8String, 1);
        setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
        setenv("SIDECUE_LOCK", [config[@"LockPath"] UTF8String], 1);
        setenv("PATH", path.UTF8String, 1);
        if (chdir(appRoot.fileSystemRepresentation) != 0) {
            perror("Cannot enter application resources");
            return 1;
        }
    }

    // Py_BytesMain finalizes Python before returning. A surrounding Cocoa pool
    // would then release AppKit objects that can still call PyObjC methods.
    // Keep the bootstrap pool above separate from PyObjC's own runtime pools.
    char **pythonArgv = calloc((size_t)argc + 10, sizeof(char *));
    if (!pythonArgv) return 1;
    int count = 0;
    pythonArgv[count++] = argv[0];
    pythonArgv[count++] = "-B";
    pythonArgv[count++] = "-u";
    pythonArgv[count++] = "-m";
    pythonArgv[count++] = "sidecue";
    if (argc == 1) {
        pythonArgv[count++] = "--config";
        pythonArgv[count++] = "config.toml";
        pythonArgv[count++] = "--asr-mode";
        pythonArgv[count++] = "mic";
    } else {
        for (int i = 1; i < argc; ++i) pythonArgv[count++] = argv[i];
    }
    int result = Py_BytesMain(count, pythonArgv);
    free(pythonArgv);
    fprintf(stderr, "--- native exit status=%d ---\n", result);
    return result;
}
