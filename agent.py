import json

from core.runtime import get_runtime
from intent_router import deteksi_intent
from local_router import jalankan_lokal


def _print_result(plan, graph):
    print(f"\nPlan: {plan.plan_id}")
    print(f"Status: {plan.status.value}")
    for task in graph.tasks.values():
        print(f"  - {task.task_id}: {task.status.value}")


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

        # Keep deterministic/local intents fast and cheap.
        if deteksi_intent(perintah) is not None:
            hasil_lokal = jalankan_lokal(perintah)
            if hasil_lokal is not None:
                print("\nDiproses secara lokal.")
                print("Hasil:", json.dumps(hasil_lokal, ensure_ascii=False))
                continue

        print("\nAgent sedang merencanakan dan mengeksekusi...")

        try:
            plan, graph = runtime.run_goal(perintah)
            _print_result(plan, graph)

            if plan.status.value == "COMPLETED":
                print("\nGoal selesai dan telah diverifikasi.")
            elif plan.status.value == "WAITING":
                print("\nGoal menunggu kondisi yang dapat dilanjutkan.")
            elif plan.status.value == "BLOCKED":
                print("\nGoal terblokir dan memerlukan keputusan/intervensi.")
            else:
                print("\nGoal belum selesai.")
        except Exception as error:
            print("\nAgent error:")
            print(error)


if __name__ == "__main__":
    main()
