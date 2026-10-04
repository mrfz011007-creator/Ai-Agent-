extends Node2D

const SAVE_PATH := "user://save.json"
const MAP_SIZE := Vector2i(18, 10)
const TILE := 64
const PLAYER_START := Vector2i(3, 5)
const NPC_POS := Vector2i(7, 4)
const BOSS_POS := Vector2i(15, 5)

var player := PLAYER_START
var hp := 100
var max_hp := 100
var mp := 30
var max_mp := 30
var xp := 0
var level := 1
var potions := 2
var quest_started := false
var quest_done := false
var enemy_hp := 0
var enemy_name := ""
var combat := false
var game_won := false
var defeated := false
var log_text := "Find the old ranger in the forest."
var rng := RandomNumberGenerator.new()

func _ready() -> void:
    rng.randomize()
    queue_redraw()

func _process(_delta: float) -> void:
    queue_redraw()

func _unhandled_input(event: InputEvent) -> void:
    if event.is_action_pressed("ui_up"): move_player(Vector2i.UP)
    elif event.is_action_pressed("ui_down"): move_player(Vector2i.DOWN)
    elif event.is_action_pressed("ui_left"): move_player(Vector2i.LEFT)
    elif event.is_action_pressed("ui_right"): move_player(Vector2i.RIGHT)
    elif event.is_action_pressed("ui_accept"): interact()
    elif event.is_action_pressed("ui_cancel"): save_game()
    elif event is InputEventKey and event.pressed and not event.echo:
        match event.keycode:
            KEY_A: attack(10)
            KEY_S: skill()
            KEY_P: use_potion()
            KEY_G: guard()
            KEY_L: load_game()
            KEY_R:
                if defeated: reset_game()

func move_player(dir: Vector2i) -> void:
    if combat or game_won or defeated: return
    var next := player + dir
    if next.x < 1 or next.y < 1 or next.x >= MAP_SIZE.x - 1 or next.y >= MAP_SIZE.y - 1: return
    player = next
    if player == NPC_POS and not quest_started:
        log_text = "The ranger waits here. Press Enter/Accept to speak."
    elif player == BOSS_POS and quest_done:
        start_combat("The Forgotten Warden", 70)
    elif rng.randf() < 0.12:
        start_combat("Forest Wraith", 28)

func interact() -> void:
    if combat or game_won or defeated: return
    if player == NPC_POS:
        if not quest_started:
            quest_started = true
            log_text = "Ranger: Bring me proof from a forest wraith."
        elif not quest_done:
            log_text = "Ranger: The wraiths haunt the eastern ruins."
        else:
            log_text = "Ranger: The path to the Warden is open."
    elif player == BOSS_POS and quest_done:
        start_combat("The Forgotten Warden", 70)
    else:
        log_text = "Nothing here."

func start_combat(name: String, amount: int) -> void:
    if game_won or defeated: return
    combat = true
    enemy_name = name
    enemy_hp = amount
    log_text = "Battle: " + enemy_name + ". A Attack, S Skill, G Guard, P Potion."

func attack(damage: int) -> void:
    if not combat: return
    enemy_hp -= damage
    if enemy_hp <= 0:
        combat = false
        var gained := 20 if enemy_name == "Forest Wraith" else 60
        xp += gained
        if enemy_name == "Forest Wraith" and quest_started:
            quest_done = true
            log_text = "Wraith defeated. Return to the ranger."
        else:
            game_won = true
            log_text = "The Warden falls. The forgotten seal awakens. Victory!"
        if xp >= level * 40:
            level += 1
            max_hp += 15
            hp = max_hp
            max_mp += 5
            mp = max_mp
            log_text += " Level up!"
        return
    enemy_turn(rng.randi_range(5, 10))

func skill() -> void:
    if not combat: return
    if mp < 10:
        log_text = "Not enough MP."
        return
    mp -= 10
    attack(20)

func guard() -> void:
    if not combat: return
    var retaliation := rng.randi_range(2, 5)
    hp = max(0, hp - retaliation)
    log_text = "Guard reduces the attack to %d damage." % retaliation
    if hp == 0:
        defeat()

func enemy_turn(damage: int) -> void:
    hp = max(0, hp - damage)
    if hp == 0:
        defeat()
    else:
        log_text = enemy_name + " strikes for " + str(damage) + "."

func use_potion() -> void:
    if not combat:
        log_text = "Potions can only be used during combat."
        return
    if potions <= 0:
        log_text = "No potions."
        return
    potions -= 1
    hp = min(max_hp, hp + 30)
    enemy_turn(rng.randi_range(5, 10))

func defeat() -> void:
    combat = false
    defeated = true
    log_text = "You were defeated. Press R to restart."

func reset_game() -> void:
    player = PLAYER_START
    hp = 100
    max_hp = 100
    mp = 30
    max_mp = 30
    xp = 0
    level = 1
    potions = 2
    quest_started = false
    quest_done = false
    enemy_hp = 0
    enemy_name = ""
    combat = false
    game_won = false
    defeated = false
    log_text = "Find the old ranger in the forest."

func save_game() -> void:
    var f := FileAccess.open(SAVE_PATH, FileAccess.WRITE)
    if f:
        f.store_string(JSON.stringify({
            "version": 1,
            "player": {"x": player.x, "y": player.y},
            "hp": hp,
            "max_hp": max_hp,
            "mp": mp,
            "max_mp": max_mp,
            "xp": xp,
            "level": level,
            "potions": potions,
            "quest_started": quest_started,
            "quest_done": quest_done,
            "game_won": game_won
        }))
        log_text = "Game saved."

func load_game() -> void:
    if not FileAccess.file_exists(SAVE_PATH):
        log_text = "No save found."
        return
    var f := FileAccess.open(SAVE_PATH, FileAccess.READ)
    if f == null:
        log_text = "Unable to open save."
        return
    var data = JSON.parse_string(f.get_as_text())
    if typeof(data) != TYPE_DICTIONARY or int(data.get("version", 0)) != 1:
        log_text = "Save is invalid."
        return
    var saved_player = data.get("player", {})
    if typeof(saved_player) != TYPE_DICTIONARY or not saved_player.has("x") or not saved_player.has("y"):
        log_text = "Save is invalid."
        return
    player = Vector2i(int(saved_player.x), int(saved_player.y))
    hp = int(data.get("hp", 100))
    max_hp = int(data.get("max_hp", 100))
    mp = int(data.get("mp", 30))
    max_mp = int(data.get("max_mp", 30))
    xp = int(data.get("xp", 0))
    level = int(data.get("level", 1))
    potions = int(data.get("potions", 2))
    quest_started = bool(data.get("quest_started", false))
    quest_done = bool(data.get("quest_done", false))
    game_won = bool(data.get("game_won", false))
    defeated = false
    combat = false
    log_text = "Game loaded."

func _draw() -> void:
    draw_rect(Rect2(0, 0, 1280, 720), Color("#0b1020"))
    for y in MAP_SIZE.y:
        for x in MAP_SIZE.x:
            var p := Vector2(x * TILE, y * TILE)
            var c := Color("#17283a") if (x + y) % 2 == 0 else Color("#1a3044")
            draw_rect(Rect2(p, Vector2(TILE - 2, TILE - 2)), c)
    draw_circle(Vector2(NPC_POS.x * TILE + 32, NPC_POS.y * TILE + 32), 18, Color("#d4a72c"))
    draw_circle(Vector2(BOSS_POS.x * TILE + 32, BOSS_POS.y * TILE + 32), 22, Color("#8e3d9e"))
    draw_circle(Vector2(player.x * TILE + 32, player.y * TILE + 32), 20, Color("#58a6ff"))
    draw_string(ThemeDB.fallback_font, Vector2(30, 675), "HP %d/%d   MP %d/%d   LV %d   XP %d   Potions %d" % [hp, max_hp, mp, max_mp, level, xp, potions], HORIZONTAL_ALIGNMENT_LEFT, -1, 22, Color.WHITE)
    draw_string(ThemeDB.fallback_font, Vector2(30, 705), log_text, HORIZONTAL_ALIGNMENT_LEFT, -1, 18, Color("#d8dee9"))
    if combat:
        draw_rect(Rect2(800, 80, 430, 330), Color("#111827"))
        draw_string(ThemeDB.fallback_font, Vector2(830, 125), enemy_name, HORIZONTAL_ALIGNMENT_LEFT, -1, 28, Color("#ffb4a2"))
        draw_string(ThemeDB.fallback_font, Vector2(830, 165), "Enemy HP: %d" % enemy_hp, HORIZONTAL_ALIGNMENT_LEFT, -1, 22, Color.WHITE)
        draw_string(ThemeDB.fallback_font, Vector2(830, 230), "[A] Attack  [S] Skill  [P] Potion", HORIZONTAL_ALIGNMENT_LEFT, -1, 20, Color.WHITE)
        draw_string(ThemeDB.fallback_font, Vector2(830, 265), "[G] Guard  [Esc] Save  [L] Load", HORIZONTAL_ALIGNMENT_LEFT, -1, 18, Color("#9aa4b2"))
    elif game_won:
        draw_string(ThemeDB.fallback_font, Vector2(800, 500), "VICTORY", HORIZONTAL_ALIGNMENT_LEFT, -1, 42, Color("#f2cc60"))
