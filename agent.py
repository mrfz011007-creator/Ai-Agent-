import json

from core.runtime import get_runtime
from core.request_router import is_action_request
from intent_router import deteksi_intent
from local_router import jalankan_lokal


def _print_result(plan, graph, runtime=None, printer=print):
    printer(f"\nPlan: {plan.plan_id}")
    printer(f"Status: {plan.status.value}")
    executions = getattr(runtime, "last_goal_execution", {}) if runtime is not None else {}
    for task in graph.tasks.values():
        printer(f"  - {task.task_id}: {task.status.value}")
        execution = executions.get(task.task_id, {}) if isinstance(executions, dict) else {}
        data = execution.get("data") if isinstance(execution, dict) else None
        if execution.get("tool") == "buat_file_terverifikasi" and isinstance(data, dict):
            if data.get("path"):
                printer(f"    Path relatif: {data['path']}")
            if "isi_aktual" in data:
                printer("    Isi aktual:")
                printer(data["isi_aktual"])
            if "verified" in data:
                status = "LULUS" if data.get("verified") is True else "GAGAL"
                printer(f"    Verifikasi isi: {status}")
            steps = data.get("steps")
            if isinstance(steps, dict):
                details = ", ".join(
                    f"{name}={'OK' if passed else 'GAGAL'}"
                    for name, passed in steps.items()
                )
                printer(f"    Tahapan: {details}")
        if task.status.value in {"FAILED", "BLOCKED"}:
            task_result = task.result if isinstance(task.result, dict) else {}
            reason = task_result.get("reason") or execution.get("error")
            if reason:
                printer(f"    Penyebab: {reason}")
        elif execution and not execution.get("success") and execution.get("error"):
            printer(f"    Penyebab: {execution['error']}")


def handle_request(runtime, perintah, printer=print):
    """Route local commands, explicit actions, and ordinary chat separately."""
    if not isinstance(perintah, str) or not perintah.strip():
        return None

    perintah = perintah.strip()

    if deteksi_intent(perintah) is not None:
        hasil_lokal = jalankan_lokal(perintah)
        if hasil_lokal is not None:
            printer("\nDiproses secara lokal.")
            printer("Hasil:", json.dumps(hasil_lokal, ensure_ascii=False))
            return hasil_lokal

    if not is_action_request(perintah):
        try:
            chat_with_metadata = getattr(runtime, "chat_with_metadata", None)
            if callable(chat_with_metadata):
                reply = chat_with_metadata(perintah)
                jawaban = reply.answer
                printer("\n" + jawaban)
                printer("\nMetadata runtime (di luar jawaban AI):")
                if reply.provider:
                    printer(f"Provider : {reply.provider}")
                if reply.actual_model:
                    printer(f"Model aktual : {reply.actual_model}")
                    printer("Identitas model : Terverifikasi dari metadata provider")
                else:
                    if reply.requested_model:
                        printer(f"Model diminta : {reply.requested_model}")
                    printer("Identitas model : Belum dapat diverifikasi dari metadata provider")
                return jawaban
            jawaban = runtime.chat(perintah)
            printer("\n" + jawaban)
            return jawaban
        except Exception as error:
            printer("\nGagal mendapatkan jawaban dari model:")
            printer(str(error))
            return None

    printer("\nAgent sedang merencanakan dan mengeksekusi...")
    try:
        plan, graph = runtime.run_goal(perintah)
        _print_result(plan, graph, runtime=runtime, printer=printer)

        if plan.status.value == "COMPLETED":
            printer("\nGoal selesai dan telah diverifikasi.")
        elif plan.status.value == "WAITING":
            printer("\nGoal menunggu kondisi yang dapat dilanjutkan.")
        elif plan.status.value == "BLOCKED":
            printer("\nGoal terblokir dan memerlukan keputusan/intervensi.")
        else:
            printer("\nGoal belum selesai.")
        return plan, graph
    except Exception as error:
        printer("\nAgent error:")
        printer(str(error))
        return None


def main():
    runtime = get_runtime()

    while True:
        try:
            perintah = input("\nAgent > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAgent berhenti.")
            break

        if not perintah:
            continue

        if perintah.lower() == "exit":
            print("Agent berhenti.")
            break

        handle_request(runtime, perintah)


if __name__ == "__main__":
    main()
