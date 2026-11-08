#include <windows.h>
#include <stdio.h>
BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID r) {
    if (reason == DLL_PROCESS_ATTACH) {
        FILE* f = NULL;
        fopen_s(&f, "E://SteamLibrary//steamapps//common//Obscure//hzp_tools//out//hook_test.txt", "w");
        if (f) { fprintf(f, "DLL loaded, pid=%lu\n", (unsigned long)GetCurrentProcessId()); fclose(f); }
    }
    return TRUE;
}
