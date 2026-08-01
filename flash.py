#!/usr/bin/env python3
import subprocess
import sys
import os
import glob
import json
import tempfile

def find_elf(build_dir):
    # Try finding via build_info.json first
    json_paths_to_try = [
        os.path.join(build_dir, "build_info.json"),
        os.path.join(build_dir, "Debug", "build_info.json"),
        os.path.join(build_dir, "Release", "build_info.json")
    ]
    
    json_path = None
    for path in json_paths_to_try:
        if os.path.exists(path):
            json_path = path
            break

    if not json_path:
        print(f"Error: Could not find build information file (build_info.json) in {build_dir}")
        print("Please configure the project using CMake first.")
        sys.exit(1)

    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
            executable = data.get("executable")
            if executable and os.path.exists(executable):
                return executable
            else:
                print(f"Error: Executable '{executable}' from {json_path} does not exist.")
                print("Please build the project first.")
                sys.exit(1)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error: Could not read {json_path}: {e}")
        sys.exit(1)

def main():
    # Set working directory to the script's location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    build_dir = "build"
    elf_path = find_elf(build_dir)

    print(f"Found ELF file: {elf_path}")

    # Create temporary JLink script
    jlink_script = tempfile.NamedTemporaryFile(mode='w', suffix='.jlink', delete=False)
    jlink_script.write("device STM32C071\n")
    jlink_script.write("if SWD\n")
    jlink_script.write("speed auto\n")
    jlink_script.write(f"loadfile {elf_path}\n")
    jlink_script.write("r\n")
    jlink_script.write("exit\n")
    jlink_script.close()

    cmd = ["JLink", "-commandfile", jlink_script.name]

    print(f"Executing: {' '.join(cmd)}")
    try:
        # Popen allows us to stream output live
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in process.stdout:
            print(line, end="")
        process.wait()

        os.unlink(jlink_script.name)

        if process.returncode != 0:
            print(f"\nError: Flashing failed with code {process.returncode}")
            sys.exit(process.returncode)
        else:
            print("\nFlashing successful!")
    except FileNotFoundError:
        print("\nError: 'JLink' not found in PATH.")
        print("Please ensure JLink is installed and added to your System PATH.")
        sys.exit(1)

if __name__ == "__main__":
    main()
