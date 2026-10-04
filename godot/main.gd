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
var xp := 0
var level := 1
var potions := 2
var quest_started := false
var quest_done := false
var enemy_hp := 0
var enemy_name := ""
var combat := false
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

func move_player(dir: Vector2i) -> void:
    if combat: return
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
    if combat: return
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
    combat = true
    enemy_name = name
    enemy_hp = amount
    log_text = "Battle: " + enemy_name + ". Attack, Skill, Guard or Potion."

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
            log_text = "The Warden falls. The forgotten seal awakens."
        if xp >= level * 40:
            level += 1
            max_hp += 15
            hp = max_hp
            log_text += " Level up!"
        queue_redraw()
        return
    var retaliation := rng.randi_range(5, 10)
    hp = max(0, hp - retaliation)
    if hp == 0:
        combat = false
        log_text = "You were defeated. Press R in a keyboard build to restart."
    else:
        log_text = enemy_name + " strikes for " + str(retaliation) + "."

func use_potion() -> void:
    if potions <= 0:
        log_text = "No potions."
        return
    potions -= 1
    hp = min(max_hp, hp + 30)
    log_text = "Potion restored 30 HP."

func save_game() -> void:
    var f := FileAccess.open(SAVE_PATH, FileAccess.WRITE)
    if f:
        f.store_string(JSON.stringify({"version":1,"player":player,"hp":hp,"xp":xp,"level":level,"potions":potions,"quest_started":quest_started,"quest_done":quest_done}))
        log_text = "Game saved."

func load_game() -> void:
    if not FileAccess.file_exists(SAVE_PATH):
        log_text = "No save found."
        return
    var f := FileAccess.open(SAVE_PATH, FileAccess.READ)
    var data = JSON.parse_string(f.get_as_text())
    if typeof(data) != TYPE_DICTIONARY or data.get("version", 0) != 1:
        log_text = "Save is invalid."
        return
    player = Vector2i(data.player.x, data.player.y)
    hp = int(data.hp); xp = int(data.xp); level = int(data.level); potions = int(data.potions)
    quest_started = bool(data.quest_started); quest_done = bool(data.quest_done)
    log_text = "Game loaded."

func _draw() -> void:
    draw_rect(Rect2(0,0,1280,720), Color("#0b1020"))
    for y in MAP_SIZE.y:
        for x in MAP_SIZE.x:
            var p := Vector2(x*TILE, y*TILE)
            var c := Color("#17283a") if (x+y)%2 == 0 else Color("#1a3044")
            draw_rect(Rect2(p, Vector2(TILE-2,TILE-2)), c)
    draw_circle(Vector2(NPC_POS.x*TILE+32,NPC_POS.y*TILE+32), 18, Color("#d4a72c"))
    draw_circle(Vector2(BOSS_POS.x*TILE+32,BOSS_POS.y*TILE+32), 22, Color("#8e3d9e"))
    draw_circle(Vector2(player.x*TILE+32,player.y*TILE+32), 20, Color("#58a6ff"))
    draw_string(ThemeDB.fallback_font, Vector2(30, 675), "HP %d/%d   LV %d   XP %d   Potions %d" % [hp,max_hp,level,xp,potions], HORIZONTAL_ALIGNMENT_LEFT, -1, 24, Color.WHITE)
    draw_string(ThemeDB.fallback_font, Vector2(30, 705), log_text, HORIZONTAL_ALIGNMENT_LEFT, -1, 18, Color("#d8dee9"))
    if combat:
        draw_rect(Rect2(800,80,430,330), Color("#111827"))
        draw_string(ThemeDB.fallback_font, Vector2(830,125), enemy_name, HORIZONTAL_ALIGNMENT_LEFT, -1, 28, Color("#ffb4a2"))
        draw_string(ThemeDB.fallback_font, Vector2(830,165), "Enemy HP: %d" % enemy_hp, HORIZONTAL_ALIGNMENT_LEFT, -1, 22, Color.WHITE)
        draw_string(ThemeDB.fallback_font, Vector2(830,230), "[A] Attack  [S] Skill  [P] Potion", HORIZONTAL_ALIGNMENT_LEFT, -1, 20, Color.WHITE)
        draw_string(ThemeDB.fallback_font, Vector2(830,265), "[G] Guard  [Esc] Save", HORIZONTAL_ALIGNMENT_LEFT, -1, 18, Color("#9aa4b2"))
