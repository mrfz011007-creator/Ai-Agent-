extends SceneTree

const MainScript = preload("res://main.gd")

var failures: Array[String] = []

func _init() -> void:
    var main = MainScript.new()
    root.add_child(main)

    check(main.player == Vector2i(3, 5), "initial player position")
    check(main.hp == 100, "initial HP")

    main.player = main.NPC_POS
    main.interact()
    check(main.quest_started, "ranger starts quest")
    check(not main.quest_done, "quest is not complete initially")

    main.start_combat("Forest Wraith", 10)
    main.attack(100)
    check(not main.combat, "wraith combat ends after lethal attack")
    check(main.quest_done, "wraith completion marks quest done")

    main.player = main.BOSS_POS
    main.start_combat("The Forgotten Warden", 10)
    main.attack(100)
    check(main.game_won, "warden defeat marks victory")
    check(not main.combat, "warden combat ends after lethal attack")

    main.reset_game()
    main.start_combat("Forest Wraith", 10)
    main.defeat()
    check(main.defeated, "defeat state is entered")
    check(not main.combat, "combat ends on defeat")
    main.reset_game()
    check(not main.defeated and not main.game_won, "restart clears terminal states")

    if failures.is_empty():
        print("ALL GAMEPLAY TESTS PASSED")
        quit(0)
    else:
        for failure in failures:
            push_error("FAIL: " + failure)
        quit(1)

func check(condition: bool, label: String) -> void:
    if not condition:
        failures.append(label)
