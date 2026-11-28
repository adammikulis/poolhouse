package app.poolhouse.core

import app.poolhouse.core.link.encodeKey

/*
 * A sample computer to look at the app with: `java -cp <test runtime classpath> app.poolhouse.core.SampleDayKt [port]`.
 *
 * Everything it answers is canned and says so: the app shows a "CANNED SAMPLE DATA" strip for it (`canned` in /hello), and the
 * names below say "sample". It listens on loopback only; an emulator reaches it at 10.0.2.2, a phone through `adb reverse`.
 */
fun main(args: Array<String>) {
    val port = args.firstOrNull()?.toInt() ?: 7450
    val computer = SampleComputer(listen = port)
    sampleDay(computer)
    val expires = System.currentTimeMillis() / 1000 + 86_400
    for (host in listOf("10.0.2.2", "127.0.0.1")) {
        println("link for $host: poolhouse://link?v=1&pin=${computer.pin}&code=${computer.code}&at=$host%3A${computer.port}&name=Sample%20computer&expires=$expires")
    }
    println("sample computer ready on 127.0.0.1:${computer.port}")
    Thread.currentThread().join()
}

fun sampleDay(computer: SampleComputer) {
    val now = System.currentTimeMillis() / 1000
    fun ago(seconds: Long) = now - seconds
    fun get(path: String, body: String) {
        computer.answers["GET /phone/v1$path"] = { computer.ok(body) }
    }
    fun post(path: String, body: String) {
        computer.answers["POST /phone/v1$path"] = { computer.ok(body) }
    }

    val sessions = listOf(
        """{"id":"claude-sample","kind":"claude","title":"Sample: make the nightly tests pass","agent":"claude-sample","model":"claude-opus","project":"sample-project","device":"Sample studio","this_computer":true,"parent":null,"state":"asking","mode":"wake","note":"","active_at":${ago(40)},"last_text":"Canned reply: two tests still fail. May I update the fixture?","waiting":1}""",
        """{"id":"subagent-sample-a","kind":"subagent","title":"Sample: trace the failing import","agent":"claude-sample-a","model":"claude-sonnet","project":"sample-project","device":"Sample studio","this_computer":true,"parent":"claude-sample","state":"working","mode":"next_step","note":"","active_at":${ago(15)},"last_text":"Canned progress: reading tests/test_sample.py"}""",
        """{"id":"subagent-sample-b","kind":"subagent","title":"Sample: review the change","agent":"claude-sample-b","model":"claude-sonnet","project":"sample-project","device":"Sample studio","this_computer":true,"parent":"claude-sample","state":"ended","mode":"via_parent","note":"It has finished: its lead is given your message.","active_at":${ago(5400)},"last_text":"Canned review: accepted with one note."}""",
        """{"id":"delegate-sample-docs","kind":"delegate","title":"Sample: write the docs page","agent":"sample-docs","model":"sample-local-model","project":"sample-project","device":"Sample studio","this_computer":true,"parent":"claude-sample","state":"working","mode":"next_step","note":"","active_at":${ago(120)},"last_text":"Canned: drafting the second section"}""",
        """{"id":"claude-sample-other","kind":"claude","title":"Sample: plan the release notes","agent":"claude-notes","model":"claude-sonnet","project":"sample-site","device":"Sample studio","this_computer":true,"parent":null,"state":"idle","mode":"wake","note":"","active_at":${ago(900)},"last_text":"Canned reply: the draft is ready to read."}""",
        """{"id":"agent-sample-codex","kind":"agent","title":"Sample: Codex on the laptop","agent":"codex-sample","model":"sample-model","project":"sample-site","device":"Sample laptop","this_computer":false,"parent":null,"state":"idle","mode":"none","note":"This agent is on another computer: link the phone to that computer to message it.","active_at":${ago(7200)},"last_text":"Canned: idle"}""",
    )
    get("/sessions", """{"sessions":[${sessions.joinToString(",")}],"note":""}""")
    val question = """{"id":"e9","at":${ago(40)},"type":"question","question":"q-sample","expires_at":${now + 3000},"questions":[{"question":"Canned question: update the fixture to match the new output?","header":"Fixture","multiSelect":false,"options":[{"label":"Update it","description":"Rewrite the stored output"},{"label":"Keep it","description":"Fix the code instead"}]}]}"""
    get(
        "/sessions/claude-sample/events",
        """{"events":[
        {"id":"e1","at":${ago(1500)},"type":"prompt","from":"computer","text":"Canned prompt: make the nightly tests pass."},
        {"id":"e2","at":${ago(1400)},"type":"progress","text":"Bash scripts/test affected"},
        {"id":"e3","at":${ago(1300)},"type":"progress","text":"Read tests/test_sample.py"},
        {"id":"e4","at":${ago(1200)},"type":"progress","text":"Bash scripts/test all tests/test_sample.py","ok":false},
        {"id":"e5","at":${ago(1100)},"type":"reply","text":"## Canned reply\nTwo tests fail in `test_sample.py`:\n\n- `test_totals` expects the old order\n- `test_report` reads a **stale** fixture\n\n```\n2 failed, 41 passed\n```"},
        {"id":"e6","at":${ago(700)},"type":"message","message":"m-sample","text":"Canned message from the phone: keep the change small."},
        {"id":"e7","at":${ago(690)},"type":"delivered","message":"m-sample"},
        {"id":"e8","at":${ago(100)},"type":"reply","text":"Canned reply: understood. One question before I go on."},
        $question
        ],"next":"c1"}""",
    )
    get(
        "/sessions/subagent-sample-a/events",
        """{"events":[
        {"id":"a1","at":${ago(600)},"type":"prompt","from":"lead","text":"Canned task from the lead: find where the import fails."},
        {"id":"a2","at":${ago(500)},"type":"progress","text":"Grep 'import sample' src"},
        {"id":"a3","at":${ago(300)},"type":"reply","text":"Canned reply: the import moved; I am checking the callers."},
        {"id":"a4","at":${ago(60)},"type":"message","message":"m-a","text":"Canned message: which file moved it?"},
        {"id":"a5","at":${ago(15)},"type":"progress","text":"Read tests/test_sample.py"}
        ],"next":"c1"}""",
    )
    for (id in listOf("subagent-sample-b", "delegate-sample-docs", "claude-sample-other", "agent-sample-codex")) {
        get("/sessions/$id/events", """{"events":[{"id":"x1","at":${ago(900)},"type":"reply","text":"Canned history of this sample session."},{"id":"x2","at":${ago(800)},"type":"notice","kind":"permission","text":"Canned notice: a permission prompt waits at the computer."}],"next":"c1"}""")
    }
    post("/sessions/claude-sample/messages", """{"message":"m-new","state":"queued","mode":"wake","note":"Canned: delivered now"}""")
    post("/sessions/claude-sample/questions/q-sample/answer", """{"answered":true}""")

    get(
        "/devices",
        """{"pool":{"name":"Sample pool","governed":false,"policy":null},"devices":[
        {"id":"sample-studio","name":"Sample studio","kind":"computer","this_computer":true,"online":true,"busy":true,"slots":4,"free":2,"queued":1,"room":"96 GB","gpu":"Sample GPU M-series","serving":["sample-model-30b:8080"],"models":["sample-model-30b","sample-model-8b"],"version":"0.3.0","address":"","last_seen":$now,"memory":{"used_bytes":45097156608,"total_bytes":103079215104},"cpu_load":0.42,"gpus":[{"name":"Sample GPU","load":0.71}]},
        {"id":"sample-tower","name":"Sample tower","kind":"computer","this_computer":false,"online":true,"busy":false,"slots":2,"free":2,"queued":0,"room":"24 GB","gpu":"Sample GPU 24 GB","serving":[],"models":["sample-model-8b"],"version":"0.3.0","address":"192.168.1.20:7447","last_seen":${ago(5)},"gpus":[{"name":"Sample GPU 0","memory_used_bytes":2147483648,"memory_total_bytes":25769803776,"load":0.03}]},
        {"id":"sample-laptop","name":"Sample laptop","kind":"computer","this_computer":false,"online":false,"slots":0,"free":0,"room":"","gpu":"","version":"0.2.9","address":"192.168.1.31:7447","last_seen":${ago(10800)}},
        {"id":"phone:ph-0123456789ab","name":"Sample phone","kind":"phone","online":true,"pool_use":false,"version":"0.3.0","last_seen":$now}
        ],"note":""}""",
    )
    get("/models", """{"can_serve":true,"installed":[{"path":"/sample/models/sample-model-8b.gguf","name":"sample-model-8b","size_bytes":5368709120},{"path":"/sample/models/sample-embed.gguf","name":"sample-embed","size_bytes":402653184}],"running":[{"port":8080,"models":["sample-model-30b"]}]}""")
    val jobs = """{"jobs":[
        {"id":"job-sample-1","name":"Sample: render the report","state":"running","submitted_at":${ago(600)},"started_at":${ago(540)},"cancellable":true,"by":"claude-sample"},
        {"id":"job-sample-2","name":"Sample: nightly tests","state":"queued","submitted_at":${ago(60)},"cancellable":true},
        {"id":"job-sample-3","name":"Sample: build the wheel","state":"failed","started_at":${ago(8000)},"finished_at":${ago(7900)},"cancellable":false,"returncode":1}
        ]}"""
    get("/jobs", jobs)
    get("/jobs/job-sample-1/log", """{"log":"canned log line 1: reading input\ncanned log line 2: 40% rendered\ncanned log line 3: writing page 12"}""")
    get("/scheduled", """{"available":true,"jobs":[{"id":"sched-sample","name":"Sample: nightly check","when":"every day at 02:00","next_at":${now + 14400},"last_state":"done","enabled":true}]}""")
    get("/scheduled/sched-sample", """{"job":{"id":"sched-sample","name":"Sample: nightly check","when":"every day at 02:00","last_state":"done"},"log":"canned: last run passed"}""")
    val run = """{"name":"sample-docs","state":"working","doing":"running shell `canned` for 2m 4s","task":"Sample task: write the docs page for the sample project.","model":"sample-local-model","lead":"claude-sample","started":${ago(480)},"seconds":480,"edit":true,"answer":""}"""
    get("/delegates", """{"runs":[$run,{"name":"sample-fix","state":"done","doing":"ended 1h 2m ago","task":"Sample task: fix the typo","model":"sample-local-model","started":${ago(5000)},"seconds":300,"edit":true}]}""")
    get(
        "/delegates/sample-docs",
        """{"run":$run,"events":[
        {"at":${ago(470)},"kind":"state","state":"working","detail":"canned"},
        {"at":${ago(400)},"kind":"call","tool":"read","file":"docs/sample.md"},
        {"at":${ago(390)},"kind":"result","tool":"read","status":"completed","output":"canned file contents"},
        {"at":${ago(300)},"kind":"text","text":"Canned: the page needs a second section."},
        {"at":${ago(200)},"kind":"test","passed":12,"failed":0,"ok":true,"command":"scripts/test all tests/test_sample.py"}
        ],"next":900,"more":false}""",
    )
    get("/delegates/sample-docs?after=900", """{"run":$run,"events":[],"next":900,"more":false}""")
    get(
        "/inbox",
        """{"questions":[{"session":"claude-sample","question":"q-sample","expires_at":${now + 3000},"asked_at":${ago(40)},"questions":[{"question":"Canned question: update the fixture to match the new output?"}]}],
        "notices":[{"id":"n1","at":${ago(800)},"type":"notice","kind":"permission","text":"Canned: a permission prompt waits at the computer.","session":"claude-sample-other"},
        {"id":"n2","at":${ago(3600)},"type":"notice","kind":"delegate","text":"Canned: delegate sample-fix finished.","session":"delegate-sample-docs"}]}""",
    )
    get("/pool-use", """{"enabled":false,"offering":false,"model":null,"queued":0}""")
    post("/pool-use", """{"enabled":false,"offering":false,"model":null,"queued":0}""")
    get("/me", """{"phone":"ph-0123456789ab","name":"Sample phone","words":"canned","computer":"Sample computer","linked_at":${ago(3600)},"pool_use":false}""")
    get("/projects", """{"projects":[{"id":"p1","name":"sample-project","path":"/sample/sample-project","connected":true},{"id":"p2","name":"sample-site","path":"/sample/sample-site","connected":true}]}""")
}
