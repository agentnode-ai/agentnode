"""Every external program a shell script invokes, by tokenising it rather than by a list of names.

This is a TEST HELPER, and the reason it exists is a review finding. An earlier version of the
prerequisite derivation looked for an explicit vocabulary of program names, and a scan that only looks
for names it already knows cannot establish "every external program": one nobody listed is invisible to
it. Two attempts without a vocabulary were worse -- a regular expression read prose out of the
installers' own quoted refusal messages and reported programs called `address`, `and` and `refusing`.

So this tokenises. It tracks single quotes, double quotes, backslash escapes, comments, heredocs,
`$(...)` and `$((...))`, and takes the word in each COMMAND POSITION: the start of a line, or just after
`|`, `||`, `&&`, `;`, `&`, `(`, `{`, or a reserved word that introduces a command. A leading
`NAME=value` assignment is stepped over, as the shell does, and a `case` construct's patterns are
patterns rather than commands.

Then each command name is classified, and the classification is the part that can be argued with:

  a shell keyword or builtin   NOT an external program by definition. The list of them is a property of
                               the shell rather than of this repository: it is the complement of a
                               vocabulary of programs, not one.
  a function defined in the    also not external.
  same file
  everything else              EXTERNAL, and nothing is filtered out of it.

What it cannot see is returned rather than left implicit: a command built at run time out of a variable,
and a word that reached a command position and is not a plausible command name -- something the
tokeniser did not understand, which is handed back instead of being dropped. A program that another
program starts cannot be seen by reading these files at all, and anything inside a heredoc fed to an
interpreter is data to the shell and is skipped as data.
"""
from __future__ import annotations

import re

NL = chr(10)

#: Shell keywords and builtins: bash's own, plus the POSIX special builtins. Being on this list means a
#: word is NOT an external program -- which is a fact about the shell and not about this project.
BUILTINS = {
    "!", "{", "}", "[[", "]]", "case", "do", "done", "elif", "else", "esac", "fi", "for", "function",
    "if", "in", "select", "then", "time", "until", "while", "coproc",
    ".", ":", "alias", "bg", "bind", "break", "builtin", "caller", "cd", "command", "compgen",
    "complete", "compopt", "continue", "declare", "dirs", "disown", "echo", "enable", "eval", "exec",
    "exit", "export", "false", "fc", "fg", "getopts", "hash", "help", "history", "jobs", "kill", "let",
    "local", "logout", "mapfile", "popd", "printf", "pushd", "pwd", "read", "readarray", "readonly",
    "return", "set", "shift", "shopt", "source", "suspend", "test", "times", "trap", "true", "type",
    "typeset", "ulimit", "umask", "unalias", "unset", "wait", "[",
}

#: Programs whose whole purpose is to start another one. After the separator -- `--` for runuser and
#: setpriv, or simply the next word that is not an option -- comes a command, and a tokeniser that
#: stopped at the wrapper would report `runuser` where the script invokes `podman`.
A_WRAPPER = {"runuser", "setpriv", "env", "nohup", "timeout", "sudo", "doas", "chroot", "nice",
             "ionice", "stdbuf", "xargs", "time"}

#: Of those, the ones whose options TAKE VALUES as separate words -- `runuser -u NAME -- CMD`. For them
#: the command is the word after `--` and nothing before it, because guessing "the first word that is
#: not an option" made this report `agentnode-worker` as a program: it is the value of `-u`.
NEEDS_A_SEPARATOR = {"runuser", "sudo", "doas"}

#: After one of these, the next word is a command again.
OPENS_A_COMMAND = {"|", "||", "&&", ";", ";;", "&", "(", ")", "{", "}", NL,
                   "if", "then", "else", "elif", "do", "while", "until", "!", "time"}

ASSIGNMENT = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?[+]?=")
#: What a command name can look like. Not a filter on WHICH programs count -- every name that matches is
#: reported -- but on what is a name at all: a purely numeric word or one carrying shell punctuation is
#: something this tokeniser did not understand, and those are listed separately.
A_NAME = re.compile(r"\A(?!\d+\Z)[A-Za-z0-9_][A-Za-z0-9_.+-]*\Z")
A_FUNCTION = re.compile(r"\A\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_-]*)\s*\(\s*\)")
#: A word built out of a variable: this tool cannot say what it will be, and says so.
FROM_A_VARIABLE = re.compile(r"[$`]")


def words_of(text: str):
    """Yield (word, is_command_position) over the shell text, tracking quotes, comments and heredocs.

    Written out rather than regexed because the two regex attempts at this read prose out of quoted
    strings. Here a quote is a state, so the contents of one are never mistaken for code.
    """
    i, n = 0, len(text)
    word = []
    at_command = True
    pending_heredocs = []            # terminators waiting for their line to end
    quoting = None                   # None, "'" or '"'

    def flush():
        nonlocal word, at_command
        if word:
            yielded = "".join(word)
            word = []
            return yielded
        return None

    while i < n:
        ch = text[i]

        if quoting == "'":
            if ch == "'":
                quoting = None
            else:
                word.append(ch)
            i += 1
            continue
        if quoting == '"':
            if ch == "\\" and i + 1 < n:
                word.append(text[i + 1])
                i += 2
                continue
            if text.startswith("$((", i):
                # Arithmetic inside a string: not a command, and its contents are not words.
                depth, j = 0, i + 3
                while j < n:
                    if text[j] == "(":
                        depth += 1
                    elif text[j] == ")":
                        if depth == 0:
                            break
                        depth -= 1
                    j += 1
                i = j + 2 if text.startswith("))", j) else j + 1
                continue
            if text.startswith("$(", i):
                # A COMMAND SUBSTITUTION INSIDE A STRING. The shell runs it, so the words in it are
                # words, and the first of them is a command. `"$(sha256sum "$f" | cut -f1)"` hid two
                # programs from the first version of this tokeniser, which is how it was noticed.
                got = flush()
                if got is not None:
                    yield got, at_command
                inner_start = i + 2
                depth, j = 0, inner_start
                inner_quote = None
                while j < n:
                    c = text[j]
                    if inner_quote:
                        if c == "\\" and inner_quote == '"':
                            j += 2
                            continue
                        if c == inner_quote:
                            inner_quote = None
                        j += 1
                        continue
                    if c in "'\"":
                        inner_quote = c
                        j += 1
                        continue
                    if c == "(":
                        depth += 1
                    elif c == ")":
                        if depth == 0:
                            break
                        depth -= 1
                    j += 1
                for inner_word, inner_at in words_of(text[inner_start:j]):
                    yield inner_word, inner_at
                i = j + 1
                at_command = False
                continue
            if ch == '"':
                quoting = None
            else:
                word.append(ch)
            i += 1
            continue

        if ch == "\\" and i + 1 < n:
            if text[i + 1] == NL:        # a continuation: the line goes on, the word does not end
                i += 2
                continue
            word.append(text[i + 1])
            i += 2
            continue

        if ch == "'" or ch == '"':
            quoting = ch
            word.append("")             # a quoted word is still a word, even if empty
            i += 1
            continue

        if ch == "#" and not word:
            while i < n and text[i] != NL:
                i += 1
            continue

        if ch == NL:
            got = flush()
            if got is not None:
                yield got, at_command
            at_command = True
            i += 1
            # the heredocs opened on the line that just ended are consumed now
            while pending_heredocs:
                terminator = pending_heredocs.pop(0)
                while i < n:
                    end = text.find(NL, i)
                    line = text[i:end if end != -1 else n]
                    i = (end + 1) if end != -1 else n
                    if line.strip() == terminator:
                        break
            continue

        if ch in " \t":
            got = flush()
            if got is not None:
                yield got, at_command
                at_command = False
            i += 1
            continue

        # a heredoc opener: remember its terminator and skip the operator itself
        if text.startswith("<<", i) and not text.startswith("<<<", i):
            got = flush()
            if got is not None:
                yield got, at_command
                at_command = False
            j = i + 2
            if j < n and text[j] == "-":
                j += 1
            while j < n and text[j] in " \t":
                j += 1
            quote = ""
            if j < n and text[j] in "'\"":
                quote = text[j]
                j += 1
            start = j
            while j < n and (text[j].isalnum() or text[j] in "_-."):
                j += 1
            terminator = text[start:j]
            if quote and j < n and text[j] == quote:
                j += 1
            if terminator:
                pending_heredocs.append(terminator)
            i = j
            continue

        if text.startswith("$((", i):
            # ARITHMETIC, not a command context. `_waited=$((_waited + 3))` came out as a program
            # called _waited because the two-character test matched the first half of this.
            depth = 0
            j = i + 2
            while j < n:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    if depth == 0:
                        break
                    depth -= 1
                j += 1
            i = j + 2 if text.startswith("))", j) else j + 1
            continue

        if text.startswith("$(", i):
            got = flush()
            if got is not None:
                yield got, at_command
            at_command = True
            i += 2
            continue

        if text.startswith("||", i) or text.startswith("&&", i) or text.startswith(";;", i):
            got = flush()
            if got is not None:
                yield got, at_command
            at_command = True
            i += 2
            continue

        if ch in "|;&()":
            got = flush()
            if got is not None:
                yield got, at_command
            at_command = True
            i += 1
            continue

        if ch in "<>":                  # a redirection: what follows is a file, not a command
            got = flush()
            if got is not None:
                yield got, at_command
                at_command = False
            i += 1
            while i < n and text[i] in " \t":
                i += 1
            # step over the target word
            while i < n and text[i] not in " \t" + NL + "|;&()":
                i += 1
            continue

        word.append(ch)
        i += 1

    got = flush()
    if got is not None:
        yield got, at_command


def what_one_file_invokes(text: str):
    """Returns (externals, functions, builtins_seen, from_a_variable)."""
    functions = set()
    for line in text.split(NL):
        m = A_FUNCTION.match(line)
        if m:
            functions.add(m.group(1))
    externals, builtins_seen, variable, not_a_name = {}, set(), set(), set()
    expect_command = True
    in_wrapper = False               # inside `runuser ... -- CMD` and friends, waiting for CMD
    in_case = 0                      # 0: not in one, 1: before `in`, 2: reading patterns
    for word, at_command in words_of(text):
        if word == "case":
            in_case = 1
            builtins_seen.add(word)
            expect_command = False
            continue
        if in_case == 1:
            if word == "in":
                in_case = 2
            continue
        if in_case == 2:
            # Patterns, up to the `)` that ends the list: `a)`, `a|b)`, `(a|b)`. `esac` leaves it.
            if word == "esac":
                in_case = 0
                expect_command = True
            elif word == ")":
                in_case = 3          # 3: inside an arm, where commands live
                expect_command = True
            continue
        if in_case == 3:
            if word == ";;":
                in_case = 2
                continue
            if word == "esac":
                in_case = 0
                expect_command = True
                continue
        if in_wrapper:
            # Step over the wrapper's own options and their values until the program appears. `--` ends
            # them explicitly; otherwise the first word that is not an option and not an assignment is it.
            if in_wrapper == "separator":
                # Nothing before `--` can be the command: the words there are options and their values.
                if word != "--":
                    continue
                in_wrapper = "first"
                continue
            if word.startswith("-") or ASSIGNMENT.match(word):
                continue
            if word in A_WRAPPER:
                # A WRAPPER INSIDE A WRAPPER, which is how every real one of these is written here:
                # `runuser -u X -- env HOME=... TMPDIR=... podman image exists`. Stopping at the first
                # one reported `env` and left podman invisible -- and podman is the program that
                # matters on a worker.
                externals.setdefault(word, 0)
                externals[word] += 1
                in_wrapper = "separator" if word in NEEDS_A_SEPARATOR else "first"
                continue
            in_wrapper = False
            if FROM_A_VARIABLE.search(word):
                # A command built out of a variable: the same limit as anywhere else, and it belongs in
                # the same place rather than among the words this tokeniser did not understand.
                variable.add(word)
            elif A_NAME.match(word) and word not in BUILTINS:
                externals.setdefault(word, 0)
                externals[word] += 1
            elif not A_NAME.match(word):
                not_a_name.add(word)
            continue
        if not (at_command or expect_command):
            continue
        expect_command = False
        if not word:
            continue
        if ASSIGNMENT.match(word):
            expect_command = True        # the next word is the command
            continue
        if word in BUILTINS or word in OPENS_A_COMMAND:
            builtins_seen.add(word)
            if word in OPENS_A_COMMAND:
                expect_command = True
            continue
        if word in A_WRAPPER:
            # Its own name is an external program, and so is what it goes on to start.
            externals.setdefault(word, 0)
            externals[word] += 1
            in_wrapper = "separator" if word in NEEDS_A_SEPARATOR else "first"
            continue
        if word in functions:
            continue
        if FROM_A_VARIABLE.search(word):
            variable.add(word)
            continue
        if not A_NAME.match(word):
            not_a_name.add(word)
            continue
        externals.setdefault(word, 0)
        externals[word] += 1
    return externals, functions, builtins_seen, variable, not_a_name
