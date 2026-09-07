// Package schema defines the immutable v2 extraction tables. Optional protobuf
// fields remain nullable; a missing input is never silently written as zero.
package schema

const Version = "2"
const ParserVersion = "v6.0.0-alpha.0"

type Subtick struct {
	Button             *uint64  `parquet:"button"`
	Pressed            *bool    `parquet:"pressed"`
	When               *float32 `parquet:"when"`
	AnalogForwardDelta *float32 `parquet:"analog_forward_delta"`
	AnalogLeftDelta    *float32 `parquet:"analog_left_delta"`
	PitchDelta         *float32 `parquet:"pitch_delta"`
	YawDelta           *float32 `parquet:"yaw_delta"`
}

type InputHistory struct {
	RenderTickCount    *int32   `parquet:"render_tick_count"`
	RenderTickFraction *float32 `parquet:"render_tick_fraction"`
	PlayerTickCount    *int32   `parquet:"player_tick_count"`
	PlayerTickFraction *float32 `parquet:"player_tick_fraction"`
	ViewPitch          *float32 `parquet:"view_pitch"`
	ViewYaw            *float32 `parquet:"view_yaw"`
	FrameNumber        *int32   `parquet:"frame_number"`
}

type UserCmd struct {
	BasePresent              bool           `parquet:"base_present"`
	ButtonsPresent           bool           `parquet:"buttons_present"`
	ViewanglesPresent        bool           `parquet:"viewangles_present"`
	DemoID                   string         `parquet:"demo_id,dict"`
	MatchID                  *string        `parquet:"match_id,dict"`
	CommandRowID             int64          `parquet:"command_row_id"`
	RoundID                  int32          `parquet:"round_id"`
	SteamID                  *uint64        `parquet:"steam_id"`
	PlayerSlot               int32          `parquet:"player_slot"`
	PlayerName               *string        `parquet:"player_name,dict"`
	Team                     *int32         `parquet:"team"`
	Alive                    *bool          `parquet:"alive"`
	IsWarmup                 bool           `parquet:"is_warmup"`
	IsFreezeTime             bool           `parquet:"is_freeze_time"`
	DemoTick                 int64          `parquet:"demo_tick"`
	DemoFrame                int64          `parquet:"demo_frame"`
	DemoTimeSeconds          *float64       `parquet:"demo_time_seconds"`
	CommandNumber            int32          `parquet:"command_number"`
	ClientTick               int32          `parquet:"client_tick"`
	ServerTickExecuted       int32          `parquet:"server_tick_executed"`
	Buttonstate1             *uint64        `parquet:"buttonstate1"`
	Buttonstate2             *uint64        `parquet:"buttonstate2"`
	Buttonstate3             *uint64        `parquet:"buttonstate3"`
	Forwardmove              *float32       `parquet:"forwardmove"`
	Leftmove                 *float32       `parquet:"leftmove"`
	Upmove                   *float32       `parquet:"upmove"`
	MousedxRaw               *int32         `parquet:"mousedx_raw"`
	MousedyRaw               *int32         `parquet:"mousedy_raw"`
	ViewPitch                *float32       `parquet:"view_pitch"`
	ViewYaw                  *float32       `parquet:"view_yaw"`
	ViewRoll                 *float32       `parquet:"view_roll"`
	Weaponselect             *int32         `parquet:"weaponselect"`
	Impulse                  *int32         `parquet:"impulse"`
	PawnEntityHandle         *uint32        `parquet:"pawn_entity_handle"`
	SubtickMoves             []Subtick      `parquet:"subtick_moves,list"`
	InputHistory             []InputHistory `parquet:"input_history,list"`
	Attack1StartHistoryIndex *int32         `parquet:"attack1_start_history_index"`
	Attack2StartHistoryIndex *int32         `parquet:"attack2_start_history_index"`
	CommandProtobuf          []byte         `parquet:"command_protobuf"`
}

type PlayerState struct {
	DemoID          string   `parquet:"demo_id,dict"`
	RoundID         int32    `parquet:"round_id"`
	DemoTick        int64    `parquet:"demo_tick"`
	DemoFrame       int64    `parquet:"demo_frame"`
	DemoTimeSeconds *float64 `parquet:"demo_time_seconds"`
	SteamID         *uint64  `parquet:"steam_id"`
	PlayerSlot      int32    `parquet:"player_slot"`
	UserID          int32    `parquet:"user_id"`
	SpectatorUserID int32    `parquet:"spectator_user_id"`
	PlayerName      string   `parquet:"player_name,dict"`
	Team            int32    `parquet:"team"`
	Alive           bool     `parquet:"alive"`
	IsWarmup        bool     `parquet:"is_warmup"`
	IsFreezeTime    bool     `parquet:"is_freeze_time"`
	IsPaused        *bool    `parquet:"is_paused"`
	PositionX       *float64 `parquet:"position_x"`
	PositionY       *float64 `parquet:"position_y"`
	PositionZ       *float64 `parquet:"position_z"`
	VelocityX       *float64 `parquet:"velocity_x"`
	VelocityY       *float64 `parquet:"velocity_y"`
	VelocityZ       *float64 `parquet:"velocity_z"`
	ViewPitch       *float32 `parquet:"view_pitch"`
	ViewYaw         *float32 `parquet:"view_yaw"`
	Health          *int32   `parquet:"health"`
	Armor           *int32   `parquet:"armor"`
	Helmet          *bool    `parquet:"helmet"`
	Scoped          *bool    `parquet:"scoped"`
	FlashDuration   *float32 `parquet:"flash_duration"`
	ActiveWeapon    *string  `parquet:"active_weapon,dict"`
	AmmoClip        *int32   `parquet:"ammo_clip"`
	AmmoReserve     *int32   `parquet:"ammo_reserve"`
	OnGround        *bool    `parquet:"on_ground"`
	Crouching       *bool    `parquet:"crouching"`
	Walking         *bool    `parquet:"walking"`
}

type Round struct {
	DemoID        string  `parquet:"demo_id,dict"`
	RoundID       int32   `parquet:"round_id"`
	RoundNumber   int32   `parquet:"round_number"`
	Map           string  `parquet:"map,dict"`
	StartTick     int64   `parquet:"start_tick"`
	FreezeEndTick *int64  `parquet:"freeze_end_tick"`
	EndTick       *int64  `parquet:"end_tick"`
	WinnerTeam    *int32  `parquet:"winner_team"`
	IsWarmup      bool    `parquet:"is_warmup"`
	BombPlanted   bool    `parquet:"bomb_planted"`
	BombSite      *string `parquet:"bomb_site"`
}

type GameEvent struct {
	DemoID     string  `parquet:"demo_id,dict"`
	RoundID    int32   `parquet:"round_id"`
	DemoTick   int64   `parquet:"demo_tick"`
	Kind       string  `parquet:"kind,dict"`
	SteamID    *uint64 `parquet:"steam_id"`
	PlayerSlot *int32  `parquet:"player_slot"`
	Weapon     *string `parquet:"weapon,dict"`
}

type Manifest struct {
	DemoID                  string            `json:"demo_id"`
	MatchID                 string            `json:"match_id,omitempty"`
	SourcePath              string            `json:"source_path"`
	FileName                string            `json:"file_name"`
	FileSize                int64             `json:"file_size"`
	SHA256                  string            `json:"sha256"`
	Map                     string            `json:"map"`
	ServerName              string            `json:"server_name"`
	IngestedAt              string            `json:"ingested_at"`
	ParserVersion           string            `json:"parser_version"`
	ParserSchemaVersion     string            `json:"parser_schema_version"`
	ExtractorVersion        string            `json:"extractor_version"`
	ParseStatus             string            `json:"parse_status"`
	RenderStatus            string            `json:"render_status"`
	Partial                 bool              `json:"partial"`
	TickRate                float64           `json:"tick_rate"`
	CommandCount            int64             `json:"command_count"`
	StateCount              int64             `json:"state_count"`
	RoundCount              int               `json:"round_count"`
	FullPayloadCount        int64             `json:"full_payload_count"`
	DeltaPayloadCount       int64             `json:"delta_payload_count"`
	EligiblePayloadCount    int64             `json:"eligible_payload_count"`
	IneligiblePayloadCount  int64             `json:"ineligible_payload_count"`
	Warnings                map[string]int64  `json:"warnings"`
	WarningExamples         map[string]string `json:"warning_examples"`
	Files                   map[string]string `json:"files"`
	Clocks                  map[string]string `json:"clocks"`
	ButtonMasks             map[string]uint64 `json:"button_masks"`
	ButtonMappingValidation string            `json:"button_mapping_validation"`
	ValidationStatus        string            `json:"validation_status"`
}
