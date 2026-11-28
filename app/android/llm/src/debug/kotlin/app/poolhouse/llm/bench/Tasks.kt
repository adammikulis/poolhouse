/** A fixed set of questions and code tasks, the same for every model, with the checks a program can make of each reply. */
package app.poolhouse.llm.bench

import kotlin.random.Random

/** `check` says whether a reply is right as far as a program can tell; the reply itself is reported to be read. */
class Task(val name: String, val prompt: String, val check: (String) -> Boolean)

private fun said(expected: String): (String) -> Boolean = { it.trim().trim('.', '`', '*').equals(expected, ignoreCase = true) }

private val SNIPPETS = listOf(
    "const PORT = 8911;\nconst ID = \"a1b22c333d4444\";\nconst OFFSETS = [1000001, 4444, 5555555, 120012];",
    "ids = {\"u_77100\": 99.911, \"u_00117\": 11.1000, \"u_2223334\": 0.0000451}\nretry_ms = 30000",
    "ssh -p 22022 build@10.0.0.117 'tail -n 1500 /var/log/job-000774.log'",
    "commit 9f8e7d6c5b4a39281706f5e4d3c2b1a0ffeeddcc\nversion = \"2.11.100\"\nbuild = 20261127",
    "let totals = [7771117, 3330003, 912211, 455554];\nif (code === 0x00FF11AA) { return 1111; }",
    "tests/test_ledger.py:1177: AssertionError: 2299 != 2298\nexit code 137\n==== 1 failed, 2299 passed, 11 skipped in 1111.11s ====",
    "File \"/srv/app/worker.py\", line 7771, in run\nsha256:e531007218dfab990486a5de7676a6932d6ea8dea233d1f698d7c21cf8a16889",
)

/** A numbered test log with long numbers; the reply must carry two of its lines exactly. */
private fun logTask(name: String, lines: Int, seed: Int): Task {
    val random = Random(seed)
    val log = (1..lines).map { line ->
        "$line| test_case_${random.nextInt(1000, 99999)} exit=${random.nextInt(0, 256)} ms=${random.nextInt(1, 9)}" +
            "${random.nextInt(1, 9).toString().repeat(4)} rev=${random.nextLong(0x1000000000, 0xffffffffff).toString(16)}"
    }
    val first = lines / 3 + 7
    val second = lines - 11
    return Task(
        name,
        "A test log:\n\n${log.joinToString("\n")}\n\nCopy lines $first and $second of this log exactly, each on its own line, and write nothing else.",
    ) { reply -> copied(reply).lines().map { it.trim() } == listOf(log[first - 1], log[second - 1]) }
}

/** The reply's lines without a code fence around them. */
private fun copied(reply: String): String =
    reply.trim().lines().filterNot { it.startsWith("```") }.joinToString("\n") { it.trimEnd() }.trim()

val COPY_TASKS: List<Task> by lazy {
    SNIPPETS.mapIndexed { index, snippet ->
        Task("copy-${index + 1}", "Repeat the following text exactly, character for character, and write nothing else.\n\n$snippet") {
            copied(it) == snippet
        }
    } + listOf(logTask("log-150", 150, 11), logTask("log-300", 300, 23))
}

val QUALITY_TASKS: List<Task> = listOf(
    Task("multiply", "What is 347 * 29 - 158? Reply with the number only.", said("9905")),
    Task("minutes", "A train leaves at 14:35 and arrives at 17:10 the same day. How many minutes is the journey? Reply with the number only.", said("155")),
    Task("capital", "What is the capital city of Australia? Reply with the name only.", said("Canberra")),
    Task("fruits", "Name exactly three fruits that are usually red, one per line, with no other text.") {
        it.trim().lines().filter(String::isNotBlank).size == 3
    },
    Task("syllogism", "All bloops are razzies. All razzies are lazzies. Are all bloops lazzies? Reply yes or no.", said("yes")),
    Task("alias", "What does this Python print?\nx = [1, 2, 3]\ny = x\ny.append(4)\nprint(len(x))\nReply with the output only.", said("4")),
    Task(
        "bugfix",
        "This Python function should return the sum of the even numbers in xs, but it is wrong:\n\ndef f(xs):\n    total = 0\n" +
            "    for x in xs:\n        if x % 2 == 1:\n            total += x\n    return total\n\nReply with only the corrected version of the wrong line.",
    ) { it.contains("x % 2 == 0") && !it.contains("== 1") },
    Task(
        "extract",
        "Return only a JSON object with the keys name, port and enabled for this sentence: " +
            "\"The service called gateway listens on port 8443 and is switched off.\"",
    ) { reply -> listOf("\"gateway\"", "8443", "false").all { reply.contains(it) } },
    Task("mode", "Which number appears most often in [4, 9, 2, 9, 4, 9, 7, 4, 9, 2]? Reply with the number only.", said("9")),
    Task(
        "words",
        "How many words are in the sentence \"the quick grey fox jumps over the lazy dog near the old mill\"? Reply with the number only.",
        said("13"),
    ),
    Task(
        "palindrome",
        "Write a Python function is_palindrome(s) that returns True when s reads the same both ways, ignoring case and every " +
            "character that is not a letter or digit. Reply with only the code.",
    ) { it.contains("def is_palindrome") && (it.contains("isalnum") || it.contains("[^")) && it.contains("lower") },
    Task(
        "intervals",
        "Write a Python function merge(intervals) that merges overlapping [start, end] intervals and returns them sorted by start. " +
            "Touching intervals such as [1,2] and [2,3] merge. Reply with only the code.",
    ) { it.contains("def merge") && it.contains("sort") && it.contains("max(") },
)
