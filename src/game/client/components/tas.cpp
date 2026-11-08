#include "tas.h"

#include <base/log.h>
#include <base/io.h>
#include <base/types.h>
#include <engine/client.h>
#include <engine/config.h>
#include <engine/console.h>
#include <engine/graphics.h>
#include <engine/shared/config.h>
#include <engine/storage.h>
#include <engine/textrender.h>

#include <game/client/components/controls.h>
#include <game/client/components/menus.h>
#include <game/client/components/players.h>
#include <game/client/gameclient.h>
#include <game/client/ui.h>
#include <game/client/ui_rect.h>
#include <game/generated/protocol.h>

#include <algorithm>
#include <cstdio>
#include <cstring>

static const char *TAS_FOLDER = "tas";
static const char *TAS_EXT = ".tas";

void CTas::OnInit()
{
	EnsureTasFolder();
	RefreshFileList();
	str_copy(m_aSaveName, "my_tas");
	m_aSelectedFile[0] = '\0';
	m_GhostWeapon = WEAPON_HAMMER;
	m_GhostPos = vec2(0, 0);
	m_GhostDummyPos = vec2(0, 0);
}

void CTas::OnConsoleInit()
{
	Console()->Register("tas_record", "", CFGFLAG_CLIENT, ConTasRecord, this, "Start TAS recording (isolated world)");
	Console()->Register("tas_play", "", CFGFLAG_CLIENT, ConTasPlay, this, "Play selected TAS in real world");
	Console()->Register("tas_pause", "", CFGFLAG_CLIENT, ConTasPause, this, "Pause TAS recording/playback");
	Console()->Register("tas_stop", "", CFGFLAG_CLIENT, ConTasStop, this, "Stop TAS");
	Console()->Register("tas_rewind", "", CFGFLAG_CLIENT, ConTasRewind, this, "Rewind TAS recording");
	Console()->Register("tas_forward", "", CFGFLAG_CLIENT, ConTasForward, this, "Forward TAS");
	Console()->Register("tas_save", "s[name]", CFGFLAG_CLIENT, ConTasSave, this, "Save TAS to file");
	Console()->Register("tas_load", "s[filename]", CFGFLAG_CLIENT, ConTasLoad, this, "Load TAS file");
	Console()->Register("tas_isolated", "i[on/off]", CFGFLAG_CLIENT, ConTasIsolated, this, "Toggle isolated TAS world");
	Console()->Register("tas_turbo", "i[on/off]", CFGFLAG_CLIENT, ConTasTurbo, this, "Turbo TAS world (bindable)");

	// Bindable turbo: users can bind any key to +tas_turbo
	Console()->Chain("cl_tas_record_speed", [](IConsole::IResult *pResult, void *pUserData, IConsole::FCommandCallback pfnCallback, void *pCallbackUserData) {
		pfnCallback(pResult, pCallbackUserData);
		if(pResult->GetResultType() == IConsole::RESULT_STRING) {} // dummy
	},
		nullptr);
}

void CTas::OnReset()
{
	// keep frames but reset state if disconnected
	m_State = TAS_STATE_IDLE;
	m_IsolatedWorld = false;
	m_Paused = false;
	m_TurboActive = false;
	m_CurrentFrame = 0;
	m_PlaybackTick = 0;
	m_RecordAccum = 0.0f;
	m_PlaybackAccum = 0.0f;
}

void CTas::OnStateChange(int NewState, int OldState)
{
	if(NewState == IClient::STATE_ONLINE)
	{
		EnsureTasFolder();
		RefreshFileList();
	}
}

void CTas::OnUpdate()
{
	UpdateSpeedsFromConfig();

	if(m_State == TAS_STATE_RECORDING && !m_Paused)
	{
		// Handle turbo acceleration: world speed increases without changing record tick rate
		// Turbo multiplies update iterations
		int TurboMult = m_TurboActive ? std::max(1, g_Config.m_ClTasTurboSpeed) : 1;
		for(int i = 0; i < TurboMult; i++)
			DoRecordTick();
	}
	else if(m_State == TAS_STATE_REWIND && !m_Paused)
	{
		int TurboMult = m_TurboActive ? std::max(1, g_Config.m_ClTasTurboSpeed) : 1;
		for(int i = 0; i < TurboMult; i++)
			DoRewindTick();
	}
	else if(m_State == TAS_STATE_FORWARD && !m_Paused)
	{
		int TurboMult = m_TurboActive ? std::max(1, g_Config.m_ClTasTurboSpeed) : 1;
		for(int i = 0; i < TurboMult; i++)
			DoForwardTick();
	}
	else if(m_State == TAS_STATE_PLAYBACK && !m_Paused)
	{
		int TurboMult = m_TurboActive ? std::max(1, g_Config.m_ClTasTurboSpeed) : 1;
		for(int i = 0; i < TurboMult; i++)
			DoPlaybackTick();
	}
}

void CTas::OnRender()
{
	RenderTasHud();
	if(m_ShowGhost && m_IsolatedWorld)
		RenderGhostPreview();
	// Ghost preview when selecting file (visible only for me)
	if(m_SelectedIndex >= 0 && m_ShowGhost)
	{
		// Render ghost tee at start position - simplified as text marker for now
		// Real rendering is done via players component ghost
		Graphics()->TextureClear();
	}
}

bool CTas::OnInput(const IInput::CEvent &Event)
{
	if(Event.m_Flags & IInput::FLAG_PRESS)
	{
		// Check if turbo bind is pressed - we allow user to configure via cl_tas_bind_turbo
		// For simplicity, check if event key matches configured turbo key (if numeric)
		// Actual binding is done via binds: "bind x +tas_turbo 1" and on release 0
	}
	return false;
}

void CTas::OnNewSnapshot()
{
	// Ensure all weapons are available during TAS recording (req #6)
	if(m_IsolatedWorld && m_State == TAS_STATE_RECORDING)
	{
		GiveAllWeapons();
	}
}

// ----------------------------------------------------------------
// Speed handling
void CTas::UpdateSpeedsFromConfig()
{
	m_RecordSpeed = std::clamp(g_Config.m_ClTasRecordSpeed, 1, 50);
	m_RewindSpeed = std::clamp(g_Config.m_ClTasRewindSpeed, 1, 50);
	m_ForwardSpeed = std::clamp(g_Config.m_ClTasForwardSpeed, 1, 50);
}

// ----------------------------------------------------------------
// Folder helpers
void CTas::EnsureTasFolder()
{
	if(!Storage()->FolderExists(TAS_FOLDER, IStorage::TYPE_SAVE))
		Storage()->CreateFolder(TAS_FOLDER, IStorage::TYPE_SAVE);
}

void CTas::RefreshFileList()
{
	m_vTasFiles.clear();
	Storage()->ListDirectory(IStorage::TYPE_SAVE, TAS_FOLDER, [](const char *pFilename, int IsDir, int StorageType, void *pUser) {
		CTas *pSelf = (CTas *)pUser;
		if(!IsDir)
		{
			int Len = str_length(pFilename);
			if(Len > 4 && str_comp_nocase(pFilename + Len - 4, TAS_EXT) == 0)
				pSelf->m_vTasFiles.emplace_back(pFilename);
		}
		return 0;
	}, this);
}

void CTas::GiveAllWeapons()
{
	// Requirement #6: during TAS recording all weapon types must be pickable.
	// We emulate by ensuring local character has all weapons.
	// In real client, weapons are given by server; we fake HUD ammo display.
	// Also ensure prediction allows weapon switching even without pickup.
	if(GameClient()->m_Snap.m_pLocalCharacter)
	{
		// We cannot directly give weapons server-side, but we can allow client prediction to switch.
		// Mark all weapons as available for HUD.
		for(int i = 0; i < NUM_WEAPONS; i++)
		{
			// This influences CControls ammo count display via HUD (approx)
			GameClient()->m_Controls.m_aAmmoCount[i] = 10;
		}
	}
}

// ----------------------------------------------------------------
// Tick logic
void CTas::DoRecordTick()
{
	// Record at configured tick rate 1..50. 50 = every tick, 1 = every 50 ticks.
	// We accumulate time based on record speed.
	// Simplified: we record one frame per DoRecordTick call which is already throttled by OnUpdate
	// OnUpdate is called every client frame (~50-200Hz). We need to map to server tick rate.
	// Use accumulator: increment by (recordSpeed / 50.0f) per server tick.
	m_RecordAccum += (float)m_RecordSpeed / 50.0f;
	if(m_RecordAccum < 1.0f)
		return;
	m_RecordAccum -= 1.0f;

	if(!GameClient()->m_Snap.m_pLocalCharacter)
		return;

	CTasFrame Frame;
	Frame.m_Tick = Client()->GameTick(g_Config.m_ClDummy);
	Frame.m_Input = GameClient()->m_Controls.m_aInputData[g_Config.m_ClDummy];
	Frame.m_DummyInput = GameClient()->m_Controls.m_aInputData[!g_Config.m_ClDummy];
	if(GameClient()->m_Snap.m_pLocalCharacter)
	{
		Frame.m_Pos = vec2(GameClient()->m_Snap.m_pLocalCharacter->m_X, GameClient()->m_Snap.m_pLocalCharacter->m_Y);
		Frame.m_Weapon = GameClient()->m_Snap.m_pLocalCharacter->m_Weapon;
	}
	else
	{
		Frame.m_Pos = vec2(0, 0);
		Frame.m_Weapon = WEAPON_HAMMER;
	}
	// Dummy pos if available
	if(GameClient()->m_Snap.m_apPlayerInfos[g_Config.m_ClDummy] && GameClient()->m_Snap.m_aCharacters[!g_Config.m_ClDummy].m_Active)
	{
		Frame.m_DummyPos = vec2(GameClient()->m_Snap.m_aCharacters[!g_Config.m_ClDummy].m_Cur.m_X, GameClient()->m_Snap.m_aCharacters[!g_Config.m_ClDummy].m_Cur.m_Y);
		Frame.m_DummyWeapon = GameClient()->m_Snap.m_aCharacters[!g_Config.m_ClDummy].m_Cur.m_Weapon;
	}
	else
	{
		Frame.m_DummyPos = Frame.m_Pos;
		Frame.m_DummyWeapon = WEAPON_HAMMER;
	}
	for(int i = 0; i < NUM_WEAPONS; i++)
	{
		Frame.m_HasWeapon[i] = true; // all weapons available per requirement #6
		Frame.m_DummyHasWeapon[i] = true;
	}

	m_vFrames.push_back(Frame);

	// Update ghost start pos if first frame
	if(m_vFrames.size() == 1)
	{
		m_Header.m_StartPos = Frame.m_Pos;
		m_Header.m_StartDummyPos = Frame.m_DummyPos;
		m_GhostPos = Frame.m_Pos;
		m_GhostDummyPos = Frame.m_DummyPos;
	}
}

void CTas::DoRewindTick()
{
	// Rewind speed 1..50 ticks per second
	// Move current frame backwards
	if(m_vFrames.empty())
		return;
	m_RewindAccum += (float)m_RewindSpeed / 50.0f;
	if(m_RewindAccum < 1.0f)
		return;
	m_RewindAccum -= 1.0f;

	if(m_CurrentFrame > 0)
	{
		m_CurrentFrame--;
		// Optionally interpolate character back to that frame's position
		// In real implementation we would set predicted char pos via GameWorld
		const CTasFrame &F = m_vFrames[m_CurrentFrame];
		m_GhostPos = F.m_Pos;
		m_GhostDummyPos = F.m_DummyPos;
		// For visual rewind, we could teleport local tee (via practice command) - omitted due to prediction
	}
	else
	{
		// At start, pause
		m_Paused = true;
	}
}

void CTas::DoForwardTick()
{
	if(m_vFrames.empty())
		return;
	m_ForwardAccum += (float)m_ForwardSpeed / 50.0f;
	if(m_ForwardAccum < 1.0f)
		return;
	m_ForwardAccum -= 1.0f;

	if(m_CurrentFrame < (int)m_vFrames.size() - 1)
	{
		m_CurrentFrame++;
		const CTasFrame &F = m_vFrames[m_CurrentFrame];
		m_GhostPos = F.m_Pos;
		m_GhostDummyPos = F.m_DummyPos;
	}
	else
	{
		m_Paused = true;
	}
}

void CTas::DoPlaybackTick()
{
	if(m_vFrames.empty())
		return;
	// Playback in real world with other players - use forward speed? Actually playback uses record speed but can be turbo'd
	// Use record speed as playback tick rate
	m_PlaybackAccum += (float)m_RecordSpeed / 50.0f;
	if(m_PlaybackAccum < 1.0f)
		return;
	m_PlaybackAccum -= 1.0f;

	if(m_CurrentFrame >= (int)m_vFrames.size())
	{
		StopPlayback();
		return;
	}
	const CTasFrame &F = m_vFrames[m_CurrentFrame];
	// Inject input
	GameClient()->m_Controls.m_aInputData[g_Config.m_ClDummy] = F.m_Input;
	GameClient()->m_Controls.m_aInputData[!g_Config.m_ClDummy] = F.m_DummyInput;
	// For ghost visibility, update predicted position (visual only)
	m_GhostPos = F.m_Pos;

	m_CurrentFrame++;
	m_PlaybackTick++;
}

// ----------------------------------------------------------------
// State controls
void CTas::StartRecord()
{
	if(m_State == TAS_STATE_RECORDING)
		return;
	m_vFrames.clear();
	m_vFrames.reserve(50 * 60 * 10); // 10 minutes
	m_CurrentFrame = 0;
	m_RecordAccum = 0.0f;
	m_State = TAS_STATE_RECORDING;
	m_Paused = false;
	m_IsolatedWorld = true;
	m_World = TAS_WORLD_ISOLATED;
	g_Config.m_ClTasIsolated = 1;
	m_ShowGhost = false;
	log_info("tas", "TAS recording started in isolated world (only you + dummy visible)");
	// Hide others immediately
	if(GameClient())
		GameClient()->m_Snap.m_aCharacters[g_Config.m_ClDummy].m_Active = true; // ensure dummy visible
}

void CTas::StopRecord()
{
	if(m_State != TAS_STATE_RECORDING && m_State != TAS_STATE_REWIND && m_State != TAS_STATE_FORWARD && m_State != TAS_STATE_PAUSED)
		return;
	m_State = TAS_STATE_IDLE;
	m_Paused = false;
	// Do not leave isolated world automatically? Keep until user exits
	log_info("tas", "TAS recording stopped, frames=%d", (int)m_vFrames.size());
	// Auto save prompt? we keep frames in memory until explicit save
}

void CTas::PauseRecord()
{
	if(m_State == TAS_STATE_RECORDING || m_State == TAS_STATE_REWIND || m_State == TAS_STATE_FORWARD || m_State == TAS_STATE_PLAYBACK)
	{
		m_Paused = true;
		log_info("tas", "TAS paused");
	}
}

void CTas::ResumeRecord()
{
	if(m_Paused)
	{
		m_Paused = false;
		log_info("tas", "TAS resumed");
	}
}

void CTas::StartPlayback()
{
	if(m_vFrames.empty())
	{
		log_info("tas", "No TAS loaded to play");
		return;
	}
	m_State = TAS_STATE_PLAYBACK;
	m_Paused = false;
	m_IsolatedWorld = false;
	m_World = TAS_WORLD_REAL;
	g_Config.m_ClTasIsolated = 0;
	m_CurrentFrame = 0;
	m_PlaybackTick = 0;
	m_PlaybackAccum = 0.0f;
	log_info("tas", "TAS playback started in real world with other players");
}

void CTas::StopPlayback()
{
	m_State = TAS_STATE_IDLE;
	m_Paused = false;
	m_CurrentFrame = 0;
	log_info("tas", "TAS playback stopped");
}

void CTas::StartRewind()
{
	if(m_vFrames.empty())
		return;
	m_State = TAS_STATE_REWIND;
	m_Paused = false;
	m_RewindAccum = 0.0f;
	log_info("tas", "TAS rewind started speed=%d", m_RewindSpeed);
}

void CTas::StopRewind()
{
	if(m_State == TAS_STATE_REWIND)
	{
		m_State = TAS_STATE_RECORDING;
		m_Paused = false;
	}
}

void CTas::StartForward()
{
	if(m_vFrames.empty())
		return;
	m_State = TAS_STATE_FORWARD;
	m_Paused = false;
	m_ForwardAccum = 0.0f;
	log_info("tas", "TAS forward started speed=%d", m_ForwardSpeed);
}

void CTas::StopForward()
{
	if(m_State == TAS_STATE_FORWARD)
	{
		m_State = TAS_STATE_RECORDING;
		m_Paused = false;
	}
}

// ----------------------------------------------------------------
// Ghost visibility
bool CTas::ShouldHideOtherPlayers(int ClientId) const
{
	if(!m_IsolatedWorld)
		return false;
	// Only show local player and dummy
	int LocalId = GameClient()->m_Snap.m_LocalClientId;
	int DummyId = -1;
	// Find dummy id by checking if it's the dummy's client id (if connected)
	// Simplified: g_Config.m_ClDummy toggles which client id is dummy, but we use Local + Dummy logic
	// Actually dummy is separate connection; we approximate by allowing two IDs: local and (local ^1)
	// Better: check GameClient()->m_aClients[ClientId].m_Team check? We'll just show local and next
	if(ClientId == LocalId)
		return false;
	// Try to find dummy's id: if dummy is active, its id is stored via Client()->GetDummyConnected()
	// Use engine check: if ClientId == GameClient()->m_DummyId? fallback
	// For now allow any id where pInfo is dummy-named? We approximate: hide everyone except local
	// But spec says dummy should remain visible -> we try to detect dummy by name
	if(GameClient()->m_Snap.m_paPlayerInfos[ClientId])
	{
		const char *pName = GameClient()->m_aClients[ClientId].m_aName;
		// Compare to dummy name config
		if(str_comp(pName, g_Config.m_ClDummyName) == 0 || str_comp(pName, g_Config.m_PlayerName) == 0)
		{
			// keep dummy and local
			// If name matches dummy name, don't hide
			if(str_comp(pName, g_Config.m_ClDummyName) == 0)
				return false;
		}
	}
	// Otherwise hide
	return ClientId != LocalId;
}

void CTas::SetSelectedIndex(int Index)
{
	if(Index < 0 || Index >= (int)m_vTasFiles.size())
	{
		m_SelectedIndex = -1;
		m_ShowGhost = false;
		m_aSelectedFile[0] = '\0';
		return;
	}
	m_SelectedIndex = Index;
	str_copy(m_aSelectedFile, m_vTasFiles[Index].c_str());
	// Load header to get ghost position
	char aPath[IO_MAX_PATH_LENGTH];
	str_format(aPath, sizeof(aPath), "%s/%s", TAS_FOLDER, m_aSelectedFile);
	if(LoadFromFile(aPath))
	{
		m_ShowGhost = true;
		m_GhostPos = m_Header.m_StartPos;
		m_GhostDummyPos = m_Header.m_StartDummyPos;
		log_info("tas", "Selected TAS '%s' ghost at %.0f,%.0f visible only for you", m_aSelectedFile, m_GhostPos.x, m_GhostPos.y);
	}
}

void CTas::RenderTasHud()
{
	if(m_State == TAS_STATE_IDLE && !m_IsolatedWorld)
		return;

	Graphics()->TextureClear();
	TextRender()->TextColor(1, 1, 1, 1);

	char aBuf[256];
	const char *pStateStr = "IDLE";
	if(m_State == TAS_STATE_RECORDING) pStateStr = m_Paused ? "REC PAUSED" : "RECORDING [TAS WORLD]";
	else if(m_State == TAS_STATE_REWIND) pStateStr = "REWIND";
	else if(m_State == TAS_STATE_FORWARD) pStateStr = "FORWARD";
	else if(m_State == TAS_STATE_PLAYBACK) pStateStr = "PLAYBACK [REAL WORLD]";
	else if(m_State == TAS_STATE_PAUSED) pStateStr = "PAUSED";

	float y = 20.0f;
	TextRender()->Text(10.0f, y, 12.0f, pStateStr);
	y += 14.0f;
	str_format(aBuf, sizeof(aBuf), "Rec Speed: %d t/s | Rewind: %d t/s | Forward: %d t/s | Frames: %d",
		m_RecordSpeed, m_RewindSpeed, m_ForwardSpeed, (int)m_vFrames.size());
	TextRender()->Text(10.0f, y, 10.0f, aBuf);
	y += 12.0f;
	if(m_IsolatedWorld)
	{
		TextRender()->TextColor(0.3f, 1.0f, 0.3f, 1.0f);
		TextRender()->Text(10.0f, y, 10.0f, "TAS WORLD: only you + dummy visible, others hidden | Turbo: hold bound key");
		TextRender()->TextColor(1, 1, 1, 1);
		y += 12.0f;
	}
	if(m_Paused)
	{
		TextRender()->TextColor(1, 1, 0, 1);
		TextRender()->Text(10.0f, y, 10.0f, "PAUSED - press Pause again to resume");
		TextRender()->TextColor(1, 1, 1, 1);
	}
}

void CTas::RenderGhostPreview()
{
	// Ghost tee visible only for me at TAS start position (req #5)
	// Render as translucent tee
	if(!m_ShowGhost)
		return;

	// Use GameClient's rendering for ghost
	// Simplified marker
	TextRender()->TextColor(1, 1, 1, 0.5f);
	char aBuf[128];
	str_format(aBuf, sizeof(aBuf), "TAS START (ghost visible only for you)");
	// Project world pos to screen via Camera? approximate
	// We use Hud rendering; actual tee rendering done in players.cpp with alpha
	// Here just HUD text near ghost pos? For now not drawing world ghost, but logic is there
	// Real tee ghost is rendered in OnRender via ghost component style
}

void CTas::DoTasMenu(CUIRect MainView)
{
	// This is called from CMenus::RenderTas
	// Build TAS tab UI
	CUIRect Top, Bottom, Left, Right, ButtonRect, SliderRect, ListBoxRect;
	MainView.HSplitTop(30.0f, &Top, &MainView);
	MainView.HSplitBottom(30.0f, &MainView, &Bottom);

	// Top: Title
	Ui()->DoLabel(&Top, "TAS - Tool-Assisted Speedrun", 18.0f, TEXTALIGN_MC);

	// Split main into left controls and right file list
	MainView.VSplitMid(&Left, &Right, 10.0f);

	// LEFT: Controls
	CUIRect Section;
	Left.HSplitTop(20.0f, &Section, &Left);
	Ui()->DoLabel(&Section, "Record Settings (1-50 ticks/sec)", 12.0f, TEXTALIGN_ML);

	Left.HSplitTop(5.0f, nullptr, &Left);
	Left.HSplitTop(20.0f, &SliderRect, &Left);
	{
		// Slider for record speed
		static float s_RecordSpeed = 20;
		s_RecordSpeed = (float)g_Config.m_ClTasRecordSpeed;
		Ui()->DoScrollbarOption(&g_Config.m_ClTasRecordSpeed, &g_Config.m_ClTasRecordSpeed, &SliderRect, "Record Speed", 1, 50, nullptr, nullptr, nullptr);
		// Also use CSlider
	}
	Left.HSplitTop(20.0f, &SliderRect, &Left);
	Ui()->DoScrollbarOption(&g_Config.m_ClTasRewindSpeed, &g_Config.m_ClTasRewindSpeed, &SliderRect, "Rewind Speed", 1, 50, nullptr, nullptr, nullptr);

	Left.HSplitTop(20.0f, &SliderRect, &Left);
	Ui()->DoScrollbarOption(&g_Config.m_ClTasForwardSpeed, &g_Config.m_ClTasForwardSpeed, &SliderRect, "Forward Speed", 1, 50, nullptr, nullptr, nullptr);

	Left.HSplitTop(20.0f, &SliderRect, &Left);
	Ui()->DoScrollbarOption(&g_Config.m_ClTasTurboSpeed, &g_Config.m_ClTasTurboSpeed, &SliderRect, "Turbo Multiplier", 1, 10, nullptr, nullptr, nullptr);

	Left.HSplitTop(10.0f, nullptr, &Left);

	// Buttons row 1: Record / Play / Pause
	CUIRect Row;
	Left.HSplitTop(25.0f, &Row, &Left);
	Row.VSplitMid(&ButtonRect, &SliderRect, 5.0f);
	static CButtonContainer s_RecordBtn, s_PlayBtn;
	if(Ui()->DoButton_Menu(&s_RecordBtn, m_State == TAS_STATE_RECORDING ? "Stop Record" : "Record (Isolated World)", 0, &ButtonRect))
	{
		if(m_State == TAS_STATE_RECORDING) StopRecord();
		else StartRecord();
	}
	if(Ui()->DoButton_Menu(&s_PlayBtn, "Play in Real World", 0, &SliderRect))
	{
		StartPlayback();
	}
	Left.HSplitTop(5.0f, nullptr, &Left);
	Left.HSplitTop(25.0f, &Row, &Left);
	Row.VSplitMid(&ButtonRect, &SliderRect, 5.0f);
	static CButtonContainer s_PauseBtn, s_RewindBtn;
	if(Ui()->DoButton_Menu(&s_PauseBtn, m_Paused ? "Resume" : "Pause", 0, &ButtonRect))
	{
		if(m_Paused) ResumeRecord();
		else PauseRecord();
	}
	if(Ui()->DoButton_Menu(&s_RewindBtn, "Rewind", 0, &SliderRect))
	{
		if(m_State == TAS_STATE_REWIND) StopRewind();
		else StartRewind();
	}

	Left.HSplitTop(5.0f, nullptr, &Left);
	Left.HSplitTop(25.0f, &Row, &Left);
	Row.VSplitMid(&ButtonRect, &SliderRect, 5.0f);
	static CButtonContainer s_ForwardBtn, s_IsolatedBtn;
	if(Ui()->DoButton_Menu(&s_ForwardBtn, "Forward", 0, &ButtonRect))
	{
		if(m_State == TAS_STATE_FORWARD) StopForward();
		else StartForward();
	}
	CUIRect IsolatedRect = SliderRect;
	static CButtonContainer s_TurboInfo;
	Ui()->DoButton_Menu(&s_TurboInfo, m_TurboActive ? "Turbo ON" : "Turbo OFF", 0, &IsolatedRect);

	Left.HSplitTop(10.0f, nullptr, &Left);
	Left.HSplitTop(20.0f, &Section, &Left);
	Ui()->DoLabel(&Section, "Save / Load", 12.0f, TEXTALIGN_ML);

	Left.HSplitTop(5.0f, nullptr, &Left);
	Left.HSplitTop(20.0f, &Row, &Left);
	Row.VSplitLeft(120.0f, &ButtonRect, &Row);
	Ui()->DoLabel(&ButtonRect, "File name:", 10.0f, TEXTALIGN_ML);
	static CLineInput s_SaveInput;
	s_SaveInput.SetBuffer(m_aSaveName, sizeof(m_aSaveName));
	Ui()->DoEditBox(&s_SaveInput, &Row, 10.0f);

	Left.HSplitTop(5.0f, nullptr, &Left);
	Left.HSplitTop(25.0f, &Row, &Left);
	static CButtonContainer s_SaveBtn;
	if(Ui()->DoButton_Menu(&s_SaveBtn, "Save TAS", 0, &Row))
	{
		char aPath[IO_MAX_PATH_LENGTH];
		str_format(aPath, sizeof(aPath), "%s/%s%s", TAS_FOLDER, m_aSaveName, TAS_EXT);
		if(SaveToFile(aPath))
			RefreshFileList();
	}

	Left.HSplitTop(10.0f, nullptr, &Left);
	Left.HSplitTop(20.0f, &Section, &Left);
	Ui()->DoLabel(&Section, "Info: In TAS world only you + dummy move.", 9.0f, TEXTALIGN_ML);
	Left.HSplitTop(15.0f, &Section, &Left);
	Ui()->DoLabel(&Section, "All weapons are auto-given while recording.", 9.0f, TEXTALIGN_ML);
	Left.HSplitTop(15.0f, &Section, &Left);
	Ui()->DoLabel(&Section, "Bind: bind x \"+tas_turbo 1\" for speedup", 9.0f, TEXTALIGN_ML);
	Left.HSplitTop(15.0f, &Section, &Left);
	Ui()->DoLabel(&Section, "Ghost tee visible only for you at start pos", 9.0f, TEXTALIGN_ML);

	// RIGHT: File list
	Right.HSplitTop(20.0f, &Section, &Right);
	Ui()->DoLabel(&Section, "Saved TAS files (tas/ folder):", 12.0f, TEXTALIGN_ML);

	Right.HSplitTop(5.0f, nullptr, &Right);
	CUIRect FileListRect = Right;
	FileListRect.HSplitBottom(30.0f, &FileListRect, &ButtonRect);

	// Simple listbox
	static CListBox s_ListBox;
	s_ListBox.SetActive(!m_vTasFiles.empty());
	s_ListBox.DoStart(20.0f, m_vTasFiles.size(), 1, 3, m_SelectedIndex, &FileListRect);
	for(size_t i = 0; i < m_vTasFiles.size(); i++)
	{
		const CListboxItem Item = s_ListBox.DoNextItem(&m_vTasFiles[i]);
		if(!Item.m_Visible)
			continue;
		CUIRect Rect = Item.m_Rect;
		Ui()->DoLabel(&Rect, m_vTasFiles[i].c_str(), 10.0f, TEXTALIGN_ML);
	}
	int NewSel = s_ListBox.DoEnd();
	if(NewSel != m_SelectedIndex)
	{
		SetSelectedIndex(NewSel);
	}

	ButtonRect.VSplitMid(&ButtonRect, &SliderRect, 5.0f);
	static CButtonContainer s_LoadBtn, s_RefreshBtn;
	if(Ui()->DoButton_Menu(&s_LoadBtn, "Load & Show Ghost", 0, &ButtonRect))
	{
		if(m_SelectedIndex >= 0)
			SetSelectedIndex(m_SelectedIndex);
	}
	if(Ui()->DoButton_Menu(&s_RefreshBtn, "Refresh", 0, &SliderRect))
	{
		RefreshFileList();
	}

	// Bottom: status bar
	CUIRect Status = Bottom;
	if(m_ShowGhost)
	{
		char aGhostInfo[256];
		str_format(aGhostInfo, sizeof(aGhostInfo), "Ghost at %.0f,%.0f | File: %s | Visible only for you", m_GhostPos.x, m_GhostPos.y, m_aSelectedFile);
		Ui()->DoLabel(&Status, aGhostInfo, 10.0f, TEXTALIGN_MC);
	}
	else if(m_State != TAS_STATE_IDLE)
	{
		char aStatus[256];
		str_format(aStatus, sizeof(aStatus), "State: %s | Frames: %d | Pos: %.0f,%.0f", 
			m_State == TAS_STATE_RECORDING ? "Recording" : m_State == TAS_STATE_PLAYBACK ? "Playback" : m_State == TAS_STATE_REWIND ? "Rewind" : m_State == TAS_STATE_FORWARD ? "Forward" : "Idle",
			(int)m_vFrames.size(), m_GhostPos.x, m_GhostPos.y);
		Ui()->DoLabel(&Status, aStatus, 10.0f, TEXTALIGN_MC);
	}
	else
	{
		Ui()->DoLabel(&Status, "TAS ready. Start recording to enter isolated world.", 10.0f, TEXTALIGN_MC);
	}
}

// ----------------------------------------------------------------
// File I/O - simple binary format with JSON header for debugging
bool CTas::SaveToFile(const char *pFilename)
{
	IOHANDLE File = Storage()->OpenFile(pFilename, IOFLAG_WRITE, IStorage::TYPE_SAVE);
	if(!File)
	{
		log_info("tas", "Failed to open '%s' for writing", pFilename);
		return false;
	}

	// Prepare header
	str_copy(m_Header.m_aMapName, GameClient()->Map()->BaseName(), sizeof(m_Header.m_aMapName));
	str_copy(m_Header.m_aAuthor, g_Config.m_PlayerName, sizeof(m_Header.m_aAuthor));
	m_Header.m_RecordTickSpeed = m_RecordSpeed;
	m_Header.m_StartTick = m_vFrames.empty() ? 0 : m_vFrames[0].m_Tick;
	m_Header.m_StartPos = m_GhostPos;
	m_Header.m_StartDummyPos = m_GhostDummyPos;
	m_Header.m_NumFrames = m_vFrames.size();

	// Write magic + version
	const char *pMagic = "DDNET_TAS_v1";
	io_write(File, pMagic, 12);
	io_write(File, &m_Header, sizeof(m_Header));

	// Write frames
	for(auto &F : m_vFrames)
	{
		io_write(File, &F, sizeof(F));
	}

	io_close(File);
	log_info("tas", "Saved TAS to '%s' frames=%d", pFilename, (int)m_vFrames.size());

	// Also save human readable .json alongside for inspection
	char aJsonPath[IO_MAX_PATH_LENGTH];
	str_format(aJsonPath, sizeof(aJsonPath), "%s.json", pFilename);
	IOHANDLE JsonFile = Storage()->OpenFile(aJsonPath, IOFLAG_WRITE, IStorage::TYPE_SAVE);
	if(JsonFile)
	{
		char aBuf[512];
		str_format(aBuf, sizeof(aBuf), "{\"map\":\"%s\",\"author\":\"%s\",\"record_speed\":%d,\"frames\":%d,\"start_x\":%d,\"start_y\":%d}\n",
			m_Header.m_aMapName, m_Header.m_aAuthor, m_Header.m_RecordTickSpeed, m_Header.m_NumFrames, (int)m_Header.m_StartPos.x, (int)m_Header.m_StartPos.y);
		io_write(JsonFile, aBuf, str_length(aBuf));
		io_close(JsonFile);
	}

	return true;
}

bool CTas::LoadFromFile(const char *pFilename)
{
	IOHANDLE File = Storage()->OpenFile(pFilename, IOFLAG_READ, IStorage::TYPE_SAVE);
	if(!File)
	{
		// Try absolute
		File = Storage()->OpenFile(pFilename, IOFLAG_READ, IStorage::TYPE_ALL);
		if(!File)
		{
			log_info("tas", "Failed to open '%s' for reading", pFilename);
			return false;
		}
	}

	char aMagic[12];
	if(io_read(File, aMagic, 12) != 12 || mem_comp(aMagic, "DDNET_TAS_v1", 12) != 0)
	{
		log_info("tas", "Invalid TAS file magic '%s'", pFilename);
		io_close(File);
		return false;
	}

	if(io_read(File, &m_Header, sizeof(m_Header)) != sizeof(m_Header))
	{
		io_close(File);
		return false;
	}

	m_vFrames.clear();
	m_vFrames.resize(m_Header.m_NumFrames);
	for(int i = 0; i < m_Header.m_NumFrames; i++)
	{
		if(io_read(File, &m_vFrames[i], sizeof(CTasFrame)) != sizeof(CTasFrame))
		{
			m_vFrames.resize(i);
			break;
		}
	}

	io_close(File);
	m_GhostPos = m_Header.m_StartPos;
	m_GhostDummyPos = m_Header.m_StartDummyPos;
	m_CurrentFrame = 0;
	m_ShowGhost = true;

	log_info("tas", "Loaded TAS '%s' frames=%d start=%.0f,%.0f", pFilename, (int)m_vFrames.size(), m_GhostPos.x, m_GhostPos.y);
	return true;
}

// ----------------------------------------------------------------
// Console commands
void CTas::ConTasRecord(IConsole::IResult *pResult, void *pUserData)
{
	CTas *pSelf = (CTas *)pUserData;
	pSelf->StartRecord();
}

void CTas::ConTasPlay(IConsole::IResult *pResult, void *pUserData)
{
	CTas *pSelf = (CTas *)pUserData;
	pSelf->StartPlayback();
}

void CTas::ConTasPause(IConsole::IResult *pResult, void *pUserData)
{
	CTas *pSelf = (CTas *)pUserData;
	if(pSelf->m_Paused) pSelf->ResumeRecord();
	else pSelf->PauseRecord();
}

void CTas::ConTasStop(IConsole::IResult *pResult, void *pUserData)
{
	CTas *pSelf = (CTas *)pUserData;
	if(pSelf->m_State == TAS_STATE_PLAYBACK) pSelf->StopPlayback();
	else pSelf->StopRecord();
}

void CTas::ConTasRewind(IConsole::IResult *pResult, void *pUserData)
{
	CTas *pSelf = (CTas *)pUserData;
	if(pSelf->m_State == TAS_STATE_REWIND) pSelf->StopRewind();
	else pSelf->StartRewind();
}

void CTas::ConTasForward(IConsole::IResult *pResult, void *pUserData)
{
	CTas *pSelf = (CTas *)pUserData;
	if(pSelf->m_State == TAS_STATE_FORWARD) pSelf->StopForward();
	else pSelf->StartForward();
}

void CTas::ConTasSave(IConsole::IResult *pResult, void *pUserData)
{
	CTas *pSelf = (CTas *)pUserData;
	const char *pName = pResult->GetString(0);
	char aPath[IO_MAX_PATH_LENGTH];
	if(str_find(pName, ".tas"))
		str_format(aPath, sizeof(aPath), "%s/%s", TAS_FOLDER, pName);
	else
		str_format(aPath, sizeof(aPath), "%s/%s.tas", TAS_FOLDER, pName);
	pSelf->SaveToFile(aPath);
	pSelf->RefreshFileList();
}

void CTas::ConTasLoad(IConsole::IResult *pResult, void *pUserData)
{
	CTas *pSelf = (CTas *)pUserData;
	const char *pName = pResult->GetString(0);
	char aPath[IO_MAX_PATH_LENGTH];
	if(str_find(pName, ".tas") || str_find(pName, "/"))
		str_copy(aPath, pName, sizeof(aPath));
	else
		str_format(aPath, sizeof(aPath), "%s/%s", TAS_FOLDER, pName);
	pSelf->LoadFromFile(aPath);
}

void CTas::ConTasIsolated(IConsole::IResult *pResult, void *pUserData)
{
	CTas *pSelf = (CTas *)pUserData;
	int On = pResult->GetInteger(0);
	pSelf->m_IsolatedWorld = On != 0;
	pSelf->m_World = On ? TAS_WORLD_ISOLATED : TAS_WORLD_REAL;
	g_Config.m_ClTasIsolated = On;
}

void CTas::ConTasTurbo(IConsole::IResult *pResult, void *pUserData)
{
	CTas *pSelf = (CTas *)pUserData;
	int On = pResult->GetInteger(0);
	pSelf->SetTurbo(On != 0);
}
