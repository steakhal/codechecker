#!/usr/bin/env python3

import argparse
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from typing import Mapping, Optional, Sequence, Tuple, Union


def run_command(
    cmd: Union[str, Sequence[str]],
    print_error: bool = True,
    cwd: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
    shell: bool = False,
) -> Tuple[int, str, str]:
    args = shlex.split(cmd) if not shell else cmd
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=env,
            shell=shell,
            encoding="utf-8",
            universal_newlines=True,
            errors="ignore",
        )
        stdout, stderr = proc.communicate()
        retcode = proc.returncode
    except FileNotFoundError:
        return 2, "", ""
    return retcode, stdout, stderr


print_variable_c_code = """
#include <stdio.h>

#define xstr(a) str(a)
#define str(a) #a

int main() {
  printf("--%s--", xstr(VARIABLE));
}
"""

print_variable_default_run_output = """--VARIABLE--"""


def get_expected_json(
    compiler: str, compiler_args: str, source_file: str, cwd: Optional[str] = None
) -> str:
    compiler_abs = shutil.which(compiler)
    if not cwd:
        cwd = os.getcwd()
    return f"""[
	{{
		"directory": "{cwd}",
		"command": "{compiler_abs} {compiler_args}",
		"file": "{source_file}"
	}}
]"""


def get_expected_log(compiler: str, compiler_args: str, source_file: str) -> str:
    compiler_abs = shutil.which(compiler)
    return f"- Processing command: {compiler_abs} {compiler} {compiler_args} \n"


empty_env = {"PATH": "/usr/bin"}


class BuildLoggerTests(unittest.TestCase):
    def __init__(self, testName: str, logger_dir: str):
        super(BuildLoggerTests, self).__init__(testName)
        self.logger_dir = logger_dir
        self.logger_file = "/tmp/logger_test_compilation_database.json"
        self.logger_debug_file = "/tmp/logger_test_debug.log"
        self.source_file_name = "logger_test_source.cpp"
        self.source_file = "/tmp/" + self.source_file_name
        self.maxDiff = None  # Dumps the complete diff on failure

    def setUp(self):
        with open(self.source_file, "w") as f:
            f.write(print_variable_c_code)

    def tearDown(self):
        tmp_files = [
            self.logger_file,
            self.logger_debug_file,
            self.logger_debug_file + ".lock",
            "./a.out",
            "/tmp/a.out",
        ]
        for file in tmp_files:
            if os.path.isfile(file):
                os.remove(file)

    def read_actual_json_and_log(self) -> Tuple[str, str]:
        with open(self.logger_file, "r") as f:
            json = f.read()
        with open(self.logger_debug_file, "r") as f:
            log = f.read()
        return json, log

    def get_envvars(self) -> Mapping[str, str]:
        return {
            "PATH": "/usr/bin",
            "LD_PRELOAD": "ldlogger.so",
            "LD_LIBRARY_PATH": self.logger_dir + "/lib",
            "CC_LOGGER_GCC_LIKE": "gcc:g++:clang:clang++:cc:c++",
            "CC_LOGGER_FILE": self.logger_file,
            "CC_LOGGER_DEBUG_FILE": self.logger_debug_file,
        }

    def assume_successful_command(
        self,
        cmd: str,
        env: Mapping[str, str],
        cwd: Optional[str] = None,
        outs: str = "",
        errs: str = "",
    ):
        retcode, stdout, stderr = run_command(cmd=cmd, env=env, cwd=cwd, shell=True)
        self.assertEqual(stdout, outs)
        self.assertEqual(stderr, errs)
        self.assertEqual(retcode, 0)

    def assert_json_only(
        self,
        cc: str,
        cc_args: str,
        source_file: str,
        cwd: Optional[str] = None,
        run_output: str = print_variable_default_run_output,
    ):
        expected_json = get_expected_json(cc, cc_args, source_file, cwd=cwd)
        actual_json, _ = self.read_actual_json_and_log()
        self.assertEqual(actual_json, expected_json)
        parsed_json = json.loads(actual_json)

        # If check the content.
        self.assertTrue(isinstance(parsed_json, list))
        self.assertEqual(len(parsed_json), 1)
        self.assertTrue(parsed_json[0]["directory"])
        self.assertTrue(parsed_json[0]["command"])
        self.assertTrue(parsed_json[0]["file"])

        # Validate the json content.
        self.assertTrue(os.path.isdir(parsed_json[0]["directory"]))
        self.assertIn(parsed_json[0]["file"], parsed_json[0]["command"])
        self.tearDown()  # Remove auxilary files, such as 'a.out'.

        # Make sure that the given command, does compile, and the binary runs as expected.
        self.assume_successful_command(
            parsed_json[0]["command"], env=empty_env, cwd=parsed_json[0]["directory"]
        )
        self.assume_successful_command(
            "./a.out", env=empty_env, cwd=parsed_json[0]["directory"], outs=run_output
        )

    def assert_log_only(self, cc: str, cc_args: str, source_file: str):
        expected_log_suffix = get_expected_log(cc, cc_args, source_file)
        _, actual_log = self.read_actual_json_and_log()
        self.assertIn(expected_log_suffix, actual_log)

    def assert_json_and_log(
        self, cc: str, cc_args: str, source_file: str, cwd: Optional[str] = None
    ):
        # Order matters: The json check will invoke the command, and overwrite the binary.
        self.assert_log_only(cc, cc_args, source_file)
        self.assert_json_only(cc, cc_args, source_file, cwd=cwd)

    def test_compiler_path1(self):
        paths = os.getenv("PATH").split(":")
        pattern = "g++-*"
        available_gnu_compilers = [
            compiler for path in paths for compiler in glob.glob(f"{path}/{pattern}")
        ]

        if not available_gnu_compilers:
            self.skipTest(f"No compiler matches the '{pattern}' pattern in your PATH")

        for compiler_abs_path in available_gnu_compilers:
            self.tearDown()  # Cleanup the previous iteration.
            env = self.get_envvars()
            env["CC_LOGGER_GCC_LIKE"] = "g++-"
            cc = compiler_abs_path
            args = self.source_file

            self.assume_successful_command(f"{cc} {args}", env)
            self.assume_successful_command(
                "./a.out", env=empty_env, outs=print_variable_default_run_output
            )
            self.assert_json_and_log(cc, args, self.source_file)

    def test_compiler_path2(self):
        paths = os.getenv("PATH").split(":")
        pattern = "g++-*"
        available_gnu_compilers = [
            compiler for path in paths for compiler in glob.glob(f"{path}/{pattern}")
        ]

        if not available_gnu_compilers:
            self.skipTest(f"No compiler matches the '{pattern}' pattern in your PATH")

        for compiler_abs_path in available_gnu_compilers:
            self.tearDown()  # Cleanup the previous iteration.
            env = self.get_envvars()
            env["CC_LOGGER_GCC_LIKE"] = "/g++-"
            cc = compiler_abs_path
            args = self.source_file

            self.assume_successful_command(f"{cc} {args}", env)
            self.assume_successful_command(
                "./a.out", env=empty_env, outs=print_variable_default_run_output
            )
            actual_json, actual_log = self.read_actual_json_and_log()
            self.assertEqual(actual_json, "[\n]")

            expected_log_suffix = get_expected_log(
                compiler_abs_path, args, self.source_file
            )
            self.assertIn(expected_log_suffix, actual_log)
            self.assertRegexpMatches(
                actual_log,
                """ - '/usr/bin/g\+\+-\d+' does not match any program name!"""
                """ Current environment variables are: CC_LOGGER_GCC_LIKE \(/g\+\+-\)""",
            )

    def test_simple(self):
        env = self.get_envvars()
        cc = "g++"
        args = self.source_file

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_json_and_log(cc, self.source_file, self.source_file)

    def test_cpath(self):
        env = self.get_envvars()
        env["CPATH"] = "path1"
        cc = "g++"
        args = self.source_file

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_log_only(cc, args, self.source_file)
        self.assert_json_only(cc, fr"""-I path1 {self.source_file}""", self.source_file)

    def test_cpath_after_last_I(self):
        env = self.get_envvars()
        env["CPATH"] = ":path1:path2:"
        cc = "g++"
        args = fr"""-I p0 {self.source_file} -I p1 -I p2"""

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_log_only(cc, args, self.source_file)
        self.assert_json_only(
            cc, fr"""{args} -I . -I path1 -I path2 -I .""", self.source_file
        )

    def test_cplus(self):
        env = self.get_envvars()
        env["CPLUS_INCLUDE_PATH"] = "path1:path2"
        env["C_INCLUDE_PATH"] = "path3:path4"
        cc = "g++"
        args = fr"""-I p0 -isystem p1 {self.source_file}"""

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_log_only(cc, args, self.source_file)
        self.assert_json_only(
            cc,
            fr"""-I p0 -isystem p1 -isystem path1 -isystem path2 {self.source_file}""",
            self.source_file,
        )

    def test_c(self):
        env = self.get_envvars()
        env["CPLUS_INCLUDE_PATH"] = "path1:path2"
        env["C_INCLUDE_PATH"] = "path3:path4"
        cc = "gcc"
        args = fr"""-I p0 -isystem p1 {self.source_file}"""

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_log_only(cc, args, self.source_file)
        self.assert_json_only(
            cc,
            fr"""-I p0 -isystem p1 -isystem path3 -isystem path4 {self.source_file}""",
            self.source_file,
        )

    def test_cpp(self):
        env = self.get_envvars()
        env["CPLUS_INCLUDE_PATH"] = "path1:path2"
        env["C_INCLUDE_PATH"] = "path3:path4"
        cc = "gcc"
        args = fr"""-I p0 -isystem p1 -x c++ {self.source_file}"""

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_log_only(cc, args, self.source_file)
        self.assert_json_only(
            cc,
            fr"""-I p0 -isystem p1 -isystem path1 -isystem path2 -x c++ {self.source_file}""",
            self.source_file,
        )

    def test_space(self):
        env = self.get_envvars()
        cc = "gcc"
        args = fr"""-DVARIABLE=hello\ world {self.source_file}"""
        run_output = "--hello world--"

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command("./a.out", env=empty_env, outs=run_output)
        self.assert_log_only(
            cc, fr"""-DVARIABLE=hello world {self.source_file}""", self.source_file
        )
        self.assert_json_only(
            cc,
            fr"""-DVARIABLE=hello\\ world {self.source_file}""",
            self.source_file,
            run_output=run_output,
        )

    def test_space2(self):
        env = self.get_envvars()
        cc = "gcc"
        args = fr"""-DVARIABLE='hello world' {self.source_file}"""
        run_output = "--hello world--"

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command("./a.out", env=empty_env, outs=run_output)
        self.assert_log_only(
            cc, fr"""-DVARIABLE=hello world {self.source_file}""", self.source_file
        )
        self.assert_json_only(
            cc,
            fr"""-DVARIABLE=hello\\ world {self.source_file}""",
            self.source_file,
            run_output=run_output,
        )

    def test_space3(self):
        env = self.get_envvars()
        cc = "gcc"
        args = fr"""-DVARIABLE="hello world" {self.source_file}"""
        run_output = "--hello world--"

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command("./a.out", env=empty_env, outs=run_output)
        self.assert_log_only(
            cc, fr"""-DVARIABLE=hello world {self.source_file}""", self.source_file
        )
        self.assert_json_only(
            cc,
            fr"""-DVARIABLE=hello\\ world {self.source_file}""",
            self.source_file,
            run_output=run_output,
        )

    def test_quote(self):
        env = self.get_envvars()
        cc = "gcc"
        args = fr"""-DVARIABLE=\"hello\" {self.source_file}"""
        run_output = '--"hello"--'

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command("./a.out", env=empty_env, outs=run_output)
        self.assert_log_only(
            cc,
            fr"""-DVARIABLE="hello" {self.source_file}""",
            self.source_file,
        )
        self.assert_json_only(
            cc,
            fr"""-DVARIABLE=\\\"hello\\\" {self.source_file}""",
            self.source_file,
            run_output=run_output,
        )

    def test_space_quote(self):
        env = self.get_envvars()
        cc = "gcc"
        args = fr"""-DVARIABLE=he\ says:\ \"hello\ world\" {self.source_file}"""
        run_output = '--he says: "hello world"--'

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command("./a.out", env=empty_env, outs=run_output)
        self.assert_log_only(
            cc,
            fr"""-DVARIABLE=he says: "hello world" {self.source_file}""",
            self.source_file,
        )
        self.assert_json_only(
            cc,
            fr"""-DVARIABLE=he\\ says:\\ \\\"hello\\ world\\\" {self.source_file}""",
            self.source_file,
            run_output=run_output,
        )

    @unittest.expectedFailure
    def test_space_backslashes(self):
        env = self.get_envvars()
        cc = "gcc"
        args = fr"""-DVARIABLE=\\\\\\\\built\\\\ages\ \"ago\"\\\\ {self.source_file}"""
        run_output = fr"""--\\built\ages "ago"\--"""

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command("./a.out", env=empty_env, outs=run_output)
        self.assert_log_only(
            cc,
            fr"""-DVARIABLE=\\built\ages "ago"\ {self.source_file}""",
            self.source_file,
        )
        self.assert_json_only(cc, args, self.source_file)

    def test_response_file(self):
        rsp_file = self.source_file + ".rsp"
        rsp_file_content = fr"""-I p0 -isystem p1"""
        with open(rsp_file, "w") as f:
            f.write(rsp_file_content)
        env = self.get_envvars()
        cc = "clang"
        args = fr"""@{rsp_file} {self.source_file}"""
        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_log_only(cc, args, self.source_file)
        self.assert_json_only(cc, args, self.source_file)
        os.remove(rsp_file)

    def test_response_file_contain_source_file(self):
        rsp_file = self.source_file + ".rsp"
        rsp_file_content = fr"""-I p0 -isystem p1 {self.source_file}"""
        with open(rsp_file, "w") as f:
            f.write(rsp_file_content)
        env = self.get_envvars()
        cc = "clang"
        args = fr"""@{rsp_file}"""
        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_log_only(cc=cc, cc_args=args, source_file=args)
        self.assert_json_only(cc=cc, cc_args=args, source_file=args)
        os.remove(rsp_file)

    def test_compiler_abs(self):
        env = self.get_envvars()
        cc = "/usr/bin/gcc"
        args = self.source_file

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_json_and_log(cc, self.source_file, self.source_file)

    def test_include_abs1(self):
        env = self.get_envvars()
        env["CC_LOGGER_ABS_PATH"] = "1"
        cc = "gcc"
        args = fr"""-Ihello {self.source_file}"""
        cwd = os.getcwd()
        expected_args_in_json = fr"""-I{cwd}/hello {self.source_file}"""

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_log_only(cc, args, self.source_file)
        self.assert_json_only(cc, expected_args_in_json, self.source_file)

    def test_include_abs2(self):
        env = self.get_envvars()
        env["CC_LOGGER_ABS_PATH"] = "1"
        cc = "gcc"
        args = fr"""-I hello {self.source_file}"""
        cwd = os.getcwd()
        expected_args_in_json = fr"""-I {cwd}/hello {self.source_file}"""

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_log_only(cc, args, self.source_file)
        self.assert_json_only(cc, expected_args_in_json, self.source_file)

    def test_include_abs3(self):
        env = self.get_envvars()
        env["CC_LOGGER_ABS_PATH"] = "1"
        cc = "gcc"
        args = fr"""-isystem=hello {self.source_file}"""
        cwd = os.getcwd()
        expected_args_in_json = fr"""-isystem={cwd}/hello {self.source_file}"""

        self.assume_successful_command(f"{cc} {args}", env)
        self.assume_successful_command(
            "./a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_log_only(cc, args, self.source_file)
        self.assert_json_only(cc, expected_args_in_json, self.source_file)

    def test_source_abs(self):
        env = self.get_envvars()
        cc = "gcc"
        args = self.source_file

        self.assume_successful_command(f"cd /tmp && {cc} {args}", env)
        self.assume_successful_command(
            "/tmp/a.out", env=empty_env, outs=print_variable_default_run_output
        )
        self.assert_json_and_log(cc, args, self.source_file, cwd="/tmp")

    def test_valid_json(self):
        env = self.get_envvars()
        retcode, _, _ = run_command(cmd="gcc", env=env, shell=True)
        self.assertEqual(retcode, 1)
        actual_json, _ = self.read_actual_json_and_log()
        self.assertEqual(actual_json, "[\n]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unittests for the build-logger")
    parser.add_argument("logger_dir", type=str, help="The path to the build-logger")
    args = parser.parse_args()

    suite = unittest.TestSuite()
    discovered_tests = [
        method_name
        for method_name in dir(BuildLoggerTests)
        if method_name.startswith("test_") and \
           callable(getattr(BuildLoggerTests, method_name))
    ]

    for test_name in discovered_tests:
        suite.addTest(BuildLoggerTests(test_name, args.logger_dir))
    unittest.TextTestRunner(verbosity=2).run(suite)

