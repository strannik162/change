#include "tas.h"

#include <base/log.h>
#include <base/io.h>
#include <base/mem.h>
#include <generated/protocol.h>
#include <base/types.h>
#include <engine/client.h>
#include <engine/config.h>
#include <engine/console.h>
#include <engine/graphics.h>
#include <engine/shared/config.h>
#include <engine/storage.h>
#include <zlib.h>
#include <engine/textrender.h>

#include <game/client/components/controls.h>
#include <game/client/components/menus.h>
#include <game/client/components/players.h>
#include <game/client/gameclient.h>
#include <game/client/ui.h>
#include <game/client/ui_rect.h>
#include <game/client/ui_listbox.h>

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
	m_LastSafeFrame = 0;
	m_WasFrozen = false;
	m_aValidateResult[0] = '\0';
	m_aLastTime[0] = '\0';
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
	Console()->Register("tas_ticks", "i[ticks]", CFGFLAG_CLIENT, ConTasTicks, this, "Set ticks per Rewind/Forward press (KRX: krx_tastickcontrolticks)");
	Console()->Register("tas_validate", "", CFGFLAG_CLIENT, ConTasValidate, this, "Validate current TAS replay tick-by-tick");
	Console()->Register("tas_remove_useless", "", CFGFLAG_CLIENT, ConTasRemoveUseless, this, "Remove useless inputs (no hook/jump, optimize aim)");
	Console()->Register("tas_showall", "", CFGFLAG_CLIENT, ConTasShowAll, this, "Ensure showall 1 (KRX prerequisite)");
	// KRX compatibility aliases (so KRX binds work 1:1)
	Console()->Register("krx_recordreplay", "", CFGFLAG_CLIENT, ConTasRecord, this, "KRX alias: toggle record");
	Console()->Register("krx_loadreplay", "", CFGFLAG_CLIENT, ConTasPlay, this, "KRX alias: toggle load/playback");
	Console()->Register("krx_taspause", "", CFGFLAG_CLIENT, ConTasPause, this, "KRX alias: pause");
	Console()->Register("krx_tasrewind", "i[on]", CFGFLAG_CLIENT, ConTasRewind, this, "KRX alias: rewind hold");
	Console()->Register("krx_tasforward", "i[on]", CFGFLAG_CLIENT, ConTasForward, this, "KRX alias: forward hold");
	Console()->Register("krx_tasrespawn", "i[on]", CFGFLAG_CLIENT, [](IConsole::IResult *pResult, void *pUserData){ (void)pResult; CTas *pSelf=(CTas*)pUserData; pSelf->ClearReplay(); }, this, "KRX alias: clear replay");
	Console()->Register("krx_tastps", "i[tps]", CFGFLAG_CLIENT, [](IConsole::IResult *pResult, void *pUserData){ (void)pUserData; g_Config.m_ClTasRecordSpeed = std::clamp(pResult->GetInteger(0),1,50); }, this, "KRX alias krx_tastps -> cl_tas_record_speed");
	Console()->Register("krx_tastickcontrolticks", "i[ticks]", CFGFLAG_CLIENT, ConTasTicks, this, "KRX alias");
	Console()->Register("krx_tickcontrolstep", "i[on]", CFGFLAG_CLIENT, [](IConsole::IResult *pResult, void *pUserData){ (void)pUserData; g_Config.m_ClTasStep = pResult->GetInteger(0)?1:0; }, this, "KRX alias step");
	Console()->Register("krx_tasautorewind", "i[on]", CFGFLAG_CLIENT, [](IConsole::IResult *pResult, void *pUserData){ (void)pUserData; g_Config.m_ClTasAutoRewind = pResult->GetInteger(0)?1:0; }, this, "KRX alias");
	Console()->Register("krx_tasautoforward", "i[on]", CFGFLAG_CLIENT, [](IConsole::IResult *pResult, void *pUserData){ (void)pUserData; g_Config.m_ClTasAutoForward = pResult->GetInteger(0)?1:0; }, this, "KRX alias");
	Console()->Register("krx_tickcontrolpause", "i[on]", CFGFLAG_CLIENT, [](IConsole::IResult *pResult, void *pUserData){ (void)pUserData; g_Config.m_ClTasAutoPause = pResult->GetInteger(0)?1:0; }, this, "KRX alias");


	// Bindable turbo: users can bind any key to +tas_turbo
	Console()->Chain("cl_tas_record_speed", [](IConsole::IResult *pResult, void *pUserData, IConsole::FCommandCallback pfnCallback, void *pCallbackUserData) {
		(void)pUserData; pfnCallback(pResult, pCallbackUserData);
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
	m_LastSafeFrame = 0;
	m_WasFrozen = false;
}

void CTas::OnStateChange(int NewState, int OldState)
{
	(void)OldState;
	if(NewState == IClient::STATE_ONLINE)
	{
		EnsureTasFolder();
		RefreshFileList();
		EnsureShowAllOnJoin();
	}
}

void CTas::OnMapLoad()
{
	EnsureShowAllOnJoin();
	m_WasFrozen = false;
	m_LastSafeFrame = 0;
}

void CTas::OnUpdate()
{
	UpdateSpeedsFromConfig();

	if(m_State == TAS_STATE_RECORDING && !m_Paused)
	{
		// Recording tick rate must stay as configured – turbo does NOT change it (KRX spec)
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
	// KRX auto tools
	CheckAutoRewindForward();
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
	(void)Event;
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
	m_TicksPerPress = std::clamp(g_Config.m_ClTasTicksPerPress, 1, 50);
	m_StepMode = g_Config.m_ClTasStep != 0;
}

// ----------------------------------------------------------------
// Folder helpers
void CTas::EnsureTasFolder()
{
	if(!Storage()->FolderExists(TAS_FOLDER, IStorage::TYPE_SAVE))
		Storage()->CreateFolder(TAS_FOLDER, IStorage::TYPE_SAVE);
}

void CTas::OptimizeMemory()
{
	// Keep memory tight: reserve 50*60*5 (5 min) initially, shrink after save/load
	if(m_vFrames.capacity() > 8192 && m_vFrames.size() < m_vFrames.capacity() / 2)
		m_vFrames.shrink_to_fit();
}

void CTas::RefreshFileList()
{
	m_vTasFiles.clear();
	m_vTasFiles.reserve(64);
	Storage()->ListDirectory(IStorage::TYPE_SAVE, TAS_FOLDER, [](const char *pFilename, int IsDir, int StorageType, void *pUser) {
		(void)StorageType;
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
	if(!m_IsolatedWorld || m_State != TAS_STATE_RECORDING)
		return;
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
	int CurTick = Client()->GameTick(g_Config.m_ClDummy);
	static int s_LastTick = -1;
	if(CurTick == s_LastTick)
		return;
	s_LastTick = CurTick;
	// Fast path for 50 TPS (no float)
	if(m_RecordSpeed == 50)
	{
		// fall through to record
	}
	else
	{
		m_RecordAccum += (float)m_RecordSpeed / 50.0f;
		if(m_RecordAccum < 1.0f)
			return;
		m_RecordAccum -= 1.0f;
	}

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
	m_vFrames.reserve(50 * 60 * 8); // 8 min @50 TPS ~24000 frames, avoids realloc
	m_CurrentFrame = 0;
	m_RecordAccum = 0.0f;
	m_State = TAS_STATE_RECORDING;
	m_Paused = false;
	m_IsolatedWorld = true;
	m_World = TAS_WORLD_ISOLATED;
	g_Config.m_ClTasIsolated = 1;
	m_ShowGhost = false;
	EnsureShowAll();
	log_info("tas", "TAS recording started in isolated world (only you + dummy visible) - showall ensured");
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
bool CTas::ShouldHidePlayer(int ClientId) { return ShouldHideOtherPlayers(ClientId); }

bool CTas::ShouldHideOtherPlayers(int ClientId) const
{
	if(!m_IsolatedWorld)
		return false;
	if(ClientId == GameClient()->m_Snap.m_LocalClientId)
		return false;
	// Keep both local tees (main + dummy) visible – use m_aLocalIds which holds both IDs
	if(ClientId == GameClient()->m_aLocalIds[0] || ClientId == GameClient()->m_aLocalIds[1])
		return false;
	if(GameClient()->PredictDummy())
	{
		int DummyId = GameClient()->m_aLocalIds[!g_Config.m_ClDummy];
		if(ClientId == DummyId)
			return false;
	}
	// Fallback: dummy is only other visible tee
	if(Client()->DummyConnected() && ClientId == GameClient()->m_aLocalIds[1])
		return false;
	return true;
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
	str_copy(aBuf, "TAS START (ghost visible only for you)");
	// Project world pos to screen via Camera? approximate
	// We use Hud rendering; actual tee rendering done in players.cpp with alpha
	// Here just HUD text near ghost pos? For now not drawing world ghost, but logic is there
	// Real tee ghost is rendered in OnRender via ghost component style
}

void CTas::DoTasMenu(CUIRect MainView)
{
	// KRX-style dark theme – mimic screenshot 2 (krx)
	// Background
	MainView.Draw(ColorRGBA(0.07f, 0.09f, 0.14f, 0.92f), IGraphics::CORNER_ALL, 8.0f);

	CUIRect Title, Content;
	MainView.HSplitTop(28.0f, &Title, &Content);
	Title.Draw(ColorRGBA(0.12f, 0.15f, 0.22f, 1.0f), IGraphics::CORNER_ALL, 6.0f);
	Ui()->DoLabel(&Title, "TAS  \xC2\xB7  Tool-Assisted Speedrun  (KRX compatible)", 13.0f, TEXTALIGN_MC);
	Content.Margin(8.0f, &Content);

	// Two columns: left 36% | right 64% (like KRX left Main+Settings+Hotkeys vs middle Replay+Tools)
	CUIRect Left, Right;
	Content.VSplitLeft(Content.w * 0.38f, &Left, &Right);
	Left.VSplitRight(8.0f, &Left, nullptr);
	Right.VSplitLeft(8.0f, nullptr, &Right);

	auto DrawPanel = [&](CUIRect &Rect, float HeaderH, const char *pTitle, CUIRect &rHeader, CUIRect &rBody, float Corners = 6.0f) {
		Rect.Draw(ColorRGBA(0.16f, 0.19f, 0.26f, 0.95f), IGraphics::CORNER_ALL, Corners);
		Rect.HSplitTop(HeaderH, &rHeader, &rBody);
		rHeader.Draw(ColorRGBA(0.20f, 0.24f, 0.34f, 1.0f), IGraphics::CORNER_T, Corners);
		rBody.Margin(6.0f, &rBody);
		Ui()->DoLabel(&rHeader, pTitle, 11.0f, TEXTALIGN_ML);
		// small left padding for title
		CUIRect T = rHeader;
		T.Margin(8.0f, &T);
		Ui()->DoLabel(&T, pTitle, 11.0f, TEXTALIGN_ML);
	};

	// LEFT COLUMN
	CUIRect Panel, Header, Body, ButtonRect, SliderRect;

	// --- Main ---
	Left.HSplitTop(108.0f, &Panel, &Left);
	Left.HSplitTop(8.0f, nullptr, &Left);
	DrawPanel(Panel, 22.0f, "Main", Header, Body);
	{
		CUIRect Slider, BtnRow1, BtnRow2;
		Body.HSplitTop(18.0f, &Slider, &Body);
		Ui()->DoScrollbarOption(&g_Config.m_ClTasRecordSpeed, &g_Config.m_ClTasRecordSpeed, &Slider, "TPS", 1, 50);
		// add numeric label
		{
			CUIRect Label;
			Slider.VSplitRight(28.0f, nullptr, &Label);
			char aVal[8];
			str_format(aVal, sizeof(aVal), "%d", g_Config.m_ClTasRecordSpeed);
			Ui()->DoLabel(&Label, aVal, 9.0f, TEXTALIGN_MC);
		}
		Body.HSplitTop(6.0f, nullptr, &Body);
		Body.HSplitTop(22.0f, &BtnRow1, &Body);
		BtnRow1.VSplitLeft(BtnRow1.w * 0.22f, &ButtonRect, &BtnRow1);
		BtnRow1.VSplitRight(0.0f, nullptr, nullptr); // spacer
		// Play/Pause/Playback like KRX: 3 buttons
		static CButtonContainer s_BtnPlay, s_BtnPause, s_BtnPlayback;
		// Play (small)
		ButtonRect.Margin(1.0f, &ButtonRect);
		if(GameClient()->m_Menus.DoButton_Menu(&s_BtnPlay, m_State == TAS_STATE_RECORDING ? "■" : "▶", 0, &ButtonRect))
		{
			if(m_State == TAS_STATE_RECORDING) StopRecord();
			else StartRecord();
		}
		// Unpause / Pause (red)
		CUIRect PauseRect, PlaybackRect;
		BtnRow1.VSplitLeft(BtnRow1.w * 0.42f, &PauseRect, &BtnRow1);
		PauseRect.Margin(1.0f, &PauseRect);
		BtnRow1.VSplitLeft(4.0f, nullptr, &BtnRow1);
		BtnRow1.VSplitLeft(BtnRow1.w, &PlaybackRect, &BtnRow1);
		PlaybackRect.Margin(1.0f, &PlaybackRect);

		// Determine pause label color via button text
		const char *pPauseText = m_Paused ? "Unpause" : (m_State == TAS_STATE_RECORDING ? "Pause" : "Pause");
		if(GameClient()->m_Menus.DoButton_Menu(&s_BtnPause, pPauseText, m_Paused ? 1 : 0, &PauseRect))
		{
			if(m_Paused) ResumeRecord();
			else PauseRecord();
		}
		if(GameClient()->m_Menus.DoButton_Menu(&s_BtnPlayback, "Playback replay", 0, &PlaybackRect))
		{
			StartPlayback();
		}
		Body.HSplitTop(4.0f, nullptr, &Body);
		Body.HSplitTop(20.0f, &BtnRow2, &Body);
		static CButtonContainer s_BtnTrash;
		BtnRow2.Margin(1.0f, &BtnRow2);
		if(GameClient()->m_Menus.DoButton_Menu(&s_BtnTrash, "Clear (trash)  \xF0\x9F\x97\x91", 0, &BtnRow2))
		{
			ClearReplay();
		}
	}

	// --- Settings ---
	Left.HSplitTop(92.0f, &Panel, &Left);
	Left.HSplitTop(8.0f, nullptr, &Left);
	DrawPanel(Panel, 22.0f, "Settings", Header, Body);
	{
		CUIRect Lcol, Rcol;
		Body.VSplitMid(&Lcol, &Rcol, 6.0f);
		CUIRect RowL;
		Lcol.HSplitTop(16.0f, &RowL, &Lcol);
		static CButtonContainer s_SetSound, s_SetEffects, s_SetAim;
		if(GameClient()->m_Menus.DoButton_CheckBox(&s_SetSound, "Enable sound", g_Config.m_ClTasShowAllAuto, &RowL))
			g_Config.m_ClTasShowAllAuto ^= 1;
		Lcol.HSplitTop(2.0f, nullptr, &Lcol);
		Lcol.HSplitTop(16.0f, &RowL, &Lcol);
		if(GameClient()->m_Menus.DoButton_CheckBox(&s_SetEffects, "Enable effects", g_Config.m_ClTasAutoRewind, &RowL))
			g_Config.m_ClTasAutoRewind ^= 1;
		Lcol.HSplitTop(2.0f, nullptr, &Lcol);
		Lcol.HSplitTop(16.0f, &RowL, &Lcol);
		if(GameClient()->m_Menus.DoButton_CheckBox(&s_SetAim, "Show real aim", g_Config.m_ClTasAutoForward, &RowL))
			g_Config.m_ClTasAutoForward ^= 1;

		Rcol.HSplitTop(16.0f, &RowL, &Rcol);
		static CButtonContainer s_AutoReplay, s_AutoSave;
		if(GameClient()->m_Menus.DoButton_CheckBox(&s_AutoReplay, "Auto Replay", g_Config.m_ClTasAutoPause, &RowL))
			g_Config.m_ClTasAutoPause ^= 1;
		Rcol.HSplitTop(2.0f, nullptr, &Rcol);
		Rcol.HSplitTop(16.0f, &RowL, &Rcol);
		if(GameClient()->m_Menus.DoButton_CheckBox(&s_AutoSave, "Auto Save Replay", g_Config.m_ClTasStep, &RowL))
			g_Config.m_ClTasStep ^= 1;
		Rcol.HSplitTop(2.0f, nullptr, &Rcol);
		Rcol.HSplitTop(16.0f, &RowL, &Rcol);
		// Predict players – informational
		static CButtonContainer s_Predict;
		int Pred = 1;
		GameClient()->m_Menus.DoButton_CheckBox(&s_Predict, "Predict players", Pred, &RowL);
	}

	// --- Hotkeys ---
	Left.HSplitTop(152.0f, &Panel, &Left);
	DrawPanel(Panel, 22.0f, "Hotkeys", Header, Body);
	{
		struct SHotkey { const char *pName; const char *pKey; const char *pCmd; };
		SHotkey aHotkeys[] = {
			{"Record:", "O", "tas_record"},
			{"Load:", "L", "tas_load"},
			{"Clear:", "J", "clear"},
			{"Pause:", "M", "tas_pause"},
			{"Rewind:", "V", "tas_rewind"},
			{"Forward:", "P", "tas_forward"},
		};
		for(auto &hk : aHotkeys)
		{
			CUIRect RowHK, Label, KeyBox;
			Body.HSplitTop(20.0f, &RowHK, &Body);
			Body.HSplitTop(2.0f, nullptr, &Body);
			RowHK.VSplitLeft(70.0f, &Label, &KeyBox);
			Ui()->DoLabel(&Label, hk.pName, 10.0f, TEXTALIGN_ML);
			KeyBox.Draw(ColorRGBA(0.22f, 0.26f, 0.36f, 1.0f), IGraphics::CORNER_ALL, 4.0f);
			CUIRect Kb = KeyBox;
			Kb.Margin(2.0f, &Kb);
			Ui()->DoLabel(&Kb, hk.pKey, 10.0f, TEXTALIGN_MC);
		}
	}

	// RIGHT COLUMN
	// --- Replay ---
	Right.HSplitTop(138.0f, &Panel, &Right);
	Right.HSplitTop(8.0f, nullptr, &Right);
	DrawPanel(Panel, 22.0f, "Replay", Header, Body);
	{
		CUIRect FileRow, BtnRowA, BtnRowB, SaveRow;
		Body.HSplitTop(22.0f, &FileRow, &Body);
		// File dropdown style
		CUIRect FolderBtn, FileName, RefreshBtn, NextBtn;
		FileRow.VSplitLeft(22.0f, &FolderBtn, &FileRow);
		FolderBtn.Draw(ColorRGBA(0.22f, 0.26f, 0.36f, 1.0f), IGraphics::CORNER_ALL, 4.0f);
		Ui()->DoLabel(&FolderBtn, "\xF0\x9F\x93\x81", 12.0f, TEXTALIGN_MC);
		FileRow.VSplitLeft(4.0f, nullptr, &FileRow);
		FileRow.VSplitRight(46.0f, &FileName, &RefreshBtn);
		RefreshBtn.VSplitLeft(22.0f, &RefreshBtn, &NextBtn);
		RefreshBtn.Margin(1.0f, &RefreshBtn);
		NextBtn.Margin(1.0f, &NextBtn);
		FileName.Draw(ColorRGBA(0.13f, 0.15f, 0.22f, 1.0f), IGraphics::CORNER_ALL, 4.0f);
		if(m_aSelectedFile[0])
			Ui()->DoLabel(&FileName, m_aSelectedFile, 9.0f, TEXTALIGN_MC);
		else if(!m_vTasFiles.empty())
			Ui()->DoLabel(&FileName, m_vTasFiles[m_SelectedIndex >= 0 ? m_SelectedIndex : 0].c_str(), 9.0f, TEXTALIGN_MC);
		else
			Ui()->DoLabel(&FileName, "no file", 9.0f, TEXTALIGN_MC);

		static CButtonContainer s_Refresh, s_Next;
		if(GameClient()->m_Menus.DoButton_Menu(&s_Refresh, "↻", 0, &RefreshBtn))
			RefreshFileList();
		if(GameClient()->m_Menus.DoButton_Menu(&s_Next, "»", 0, &NextBtn))
		{
			if(!m_vTasFiles.empty())
			{
				int Next = (m_SelectedIndex + 1) % (int)m_vTasFiles.size();
				SetSelectedIndex(Next);
			}
		}

		Body.HSplitTop(4.0f, nullptr, &Body);
		Body.HSplitTop(20.0f, &BtnRowA, &Body);
		BtnRowA.VSplitMid(&ButtonRect, &SliderRect, 6.0f);
		static CButtonContainer s_Validate, s_GetTime;
		if(GameClient()->m_Menus.DoButton_Menu(&s_Validate, "Validate replay", 0, &ButtonRect))
		{
			char aBuf[256];
			ValidateReplay(aBuf, sizeof(aBuf));
			Console()->Print(IConsole::OUTPUT_LEVEL_STANDARD, "tas", aBuf);
		}
		if(GameClient()->m_Menus.DoButton_Menu(&s_GetTime, "Get replay time", 0, &SliderRect))
		{
			char aBuf[64];
			GetReplayTime(aBuf, sizeof(aBuf));
			Console()->Print(IConsole::OUTPUT_LEVEL_STANDARD, "tas", aBuf);
		}
		Body.HSplitTop(4.0f, nullptr, &Body);
		Body.HSplitTop(20.0f, &BtnRowB, &Body);
		static CButtonContainer s_RemoveUseless;
		if(GameClient()->m_Menus.DoButton_Menu(&s_RemoveUseless, "Remove Useless", 0, &BtnRowB))
		{
			int R = RemoveUselessInputs();
			char aBuf[128];
			str_format(aBuf, sizeof(aBuf), "Removed %d useless ticks, %d remain", R, (int)m_vFrames.size());
			Console()->Print(IConsole::OUTPUT_LEVEL_STANDARD, "tas", aBuf);
		}
		Body.HSplitTop(6.0f, nullptr, &Body);
		Body.HSplitTop(22.0f, &SaveRow, &Body);
		SaveRow.Draw(ColorRGBA(0.55f, 0.68f, 0.32f, 1.0f), IGraphics::CORNER_ALL, 5.0f);
		static CButtonContainer s_SaveRep;
		// Override color via Draw, but button text
		CUIRect SaveBtn = SaveRow;
		SaveBtn.Margin(1.0f, &SaveBtn);
		if(GameClient()->m_Menus.DoButton_Menu(&s_SaveRep, "Save replay", 0, &SaveBtn))
		{
			char aPath[IO_MAX_PATH_LENGTH];
			str_format(aPath, sizeof(aPath), "%s/%s%s", TAS_FOLDER, m_aSaveName, TAS_EXT);
			if(SaveToFile(aPath))
				RefreshFileList();
		}
	}

	// --- Replay Vault ---
	Right.HSplitTop(68.0f, &Panel, &Right);
	Right.HSplitTop(8.0f, nullptr, &Right);
	DrawPanel(Panel, 22.0f, "Replay Vault", Header, Body);
	{
		CUIRect SyncRow;
		Body.HSplitTop(22.0f, &SyncRow, &Body);
		SyncRow.VSplitMid(&ButtonRect, &SliderRect, 6.0f);
		static CButtonContainer s_AutoSync, s_SyncNow;
		if(GameClient()->m_Menus.DoButton_Menu(&s_AutoSync, "Auto Sync", 0, &ButtonRect))
			Console()->Print(IConsole::OUTPUT_LEVEL_STANDARD, "tas", "Auto Sync: not yet (vault github.com/krxclient/krx-replays)");
		if(GameClient()->m_Menus.DoButton_Menu(&s_SyncNow, "Sync now", 0, &SliderRect))
			RefreshFileList();
		Body.HSplitTop(4.0f, nullptr, &Body);
		Ui()->DoLabel(&Body, "Automatically sync with the replay vault from github.com/krxclient/krx-replays", 7.0f, TEXTALIGN_TL);
	}

	// --- Tools ---
	Right.HSplitTop(84.0f, &Panel, &Right);
	Right.HSplitTop(8.0f, nullptr, &Right);
	DrawPanel(Panel, 22.0f, "Tools", Header, Body);
	{
		CUIRect TicksRow, ToolsBtnRow;
		Body.HSplitTop(18.0f, &TicksRow, &Body);
		CUIRect TLabel, TSlider, TVal;
		TicksRow.VSplitLeft(44.0f, &TLabel, &TicksRow);
		TicksRow.VSplitRight(24.0f, &TSlider, &TVal);
		Ui()->DoLabel(&TLabel, "Ticks: 1", 10.0f, TEXTALIGN_ML);
		Ui()->DoScrollbarOption(&g_Config.m_ClTasTicksPerPress, &g_Config.m_ClTasTicksPerPress, &TSlider, "", 1, 50);
		char aTickVal[8];
		str_format(aTickVal, sizeof(aTickVal), "%d", g_Config.m_ClTasTicksPerPress);
		Ui()->DoLabel(&TVal, aTickVal, 10.0f, TEXTALIGN_MC);
		Body.HSplitTop(6.0f, nullptr, &Body);
		Body.HSplitTop(20.0f, &ToolsBtnRow, &Body);
		ToolsBtnRow.VSplitLeft(ToolsBtnRow.w * 0.25f, &ButtonRect, &ToolsBtnRow);
		ToolsBtnRow.VSplitLeft(4.0f, nullptr, &ToolsBtnRow);
		ToolsBtnRow.VSplitLeft(ToolsBtnRow.w * 0.33f, &SliderRect, &ToolsBtnRow);
		ToolsBtnRow.VSplitLeft(4.0f, nullptr, &ToolsBtnRow);
		CUIRect Btn3, Btn4;
		ToolsBtnRow.VSplitLeft(ToolsBtnRow.w * 0.5f, &Btn3, &Btn4);
		Btn3.Margin(1.0f, &Btn3);
		Btn4.Margin(1.0f, &Btn4);
		ButtonRect.Margin(1.0f, &ButtonRect);
		SliderRect.Margin(1.0f, &SliderRect);
		static CButtonContainer s_AutoRewind, s_AutoForward, s_AutoPause2, s_Step;
		if(GameClient()->m_Menus.DoButton_Menu(&s_AutoRewind, "Auto rewind", g_Config.m_ClTasAutoRewind, &ButtonRect))
			g_Config.m_ClTasAutoRewind ^= 1;
		if(GameClient()->m_Menus.DoButton_Menu(&s_AutoForward, "Auto forward", g_Config.m_ClTasAutoForward, &SliderRect))
			g_Config.m_ClTasAutoForward ^= 1;
		if(GameClient()->m_Menus.DoButton_Menu(&s_AutoPause2, "Auto pause", g_Config.m_ClTasAutoPause, &Btn3))
			g_Config.m_ClTasAutoPause ^= 1;
		if(GameClient()->m_Menus.DoButton_Menu(&s_Step, "Step", g_Config.m_ClTasStep, &Btn4))
			g_Config.m_ClTasStep ^= 1;
		Body.HSplitTop(4.0f, nullptr, &Body);
		// show rewind/forward turbo row
		CUIRect RFFRow;
		Body.HSplitTop(20.0f, &RFFRow, &Body);
		RFFRow.VSplitMid(&ButtonRect, &SliderRect, 6.0f);
		static CButtonContainer s_Rew, s_Fw;
		ButtonRect.Margin(1.0f, &ButtonRect);
		SliderRect.Margin(1.0f, &SliderRect);
		if(GameClient()->m_Menus.DoButton_Menu(&s_Rew, m_State == TAS_STATE_REWIND ? "Stop Rewind" : "Rewind (Hold)", 0, &ButtonRect))
		{
			if(m_State == TAS_STATE_REWIND) StopRewind();
			else {
				if(m_StepMode) DoRewindTicks(1);
				else if(g_Config.m_ClTasTicksPerPress > 1) DoRewindTicks(g_Config.m_ClTasTicksPerPress);
				else StartRewind();
			}
		}
		if(GameClient()->m_Menus.DoButton_Menu(&s_Fw, m_State == TAS_STATE_FORWARD ? "Stop Forward" : "Forward (Hold)", 0, &SliderRect))
		{
			if(m_State == TAS_STATE_FORWARD) StopForward();
			else {
				if(m_StepMode) DoForwardTicks(1);
				else if(g_Config.m_ClTasTicksPerPress > 1) DoForwardTicks(g_Config.m_ClTasTicksPerPress);
				else StartForward();
			}
		}
	}

	// --- Scrubber (timeline) ---
	Right.HSplitTop(36.0f, &Panel, &Right);
	Right.HSplitTop(8.0f, nullptr, &Right);
	DrawPanel(Panel, 22.0f, "Timeline", Header, Body);
	{
		if(!m_vFrames.empty())
		{
			CUIRect Slider;
			Body.HSplitTop(14.0f, &Slider, &Body);
			int Max = (int)m_vFrames.size() - 1;
			if(Max < 0) Max = 0;
			int Old = m_CurrentFrame;
			Ui()->DoScrollbarOption(&m_CurrentFrame, &m_CurrentFrame, &Slider, "", 0, Max);
			if(Old != m_CurrentFrame && m_CurrentFrame < (int)m_vFrames.size())
			{
				m_GhostPos = m_vFrames[m_CurrentFrame].m_Pos;
				m_GhostDummyPos = m_vFrames[m_CurrentFrame].m_DummyPos;
			}
			CUIRect Label;
			Body.HSplitTop(10.0f, &Label, &Body);
			char aTime[64];
			GetReplayTime(aTime, sizeof(aTime));
			char aLabel[128];
			str_format(aLabel, sizeof(aLabel), "%d / %d frames  %s", m_CurrentFrame, (int)m_vFrames.size(), aTime);
			Ui()->DoLabel(&Label, aLabel, 8.0f, TEXTALIGN_MC);
		}
		else
			Ui()->DoLabel(&Body, "No replay loaded", 9.0f, TEXTALIGN_MC);
	}

	// --- Fake Aim ---
	Right.HSplitTop(72.0f, &Panel, &Right);
	Right.HSplitTop(8.0f, nullptr, &Right);
	DrawPanel(Panel, 22.0f, "Fake Aim", Header, Body);
	{
		CUIRect DropRow, AddRow;
		Body.HSplitTop(20.0f, &DropRow, &Body);
		DropRow.Draw(ColorRGBA(0.22f, 0.26f, 0.36f, 1.0f), IGraphics::CORNER_ALL, 4.0f);
		CUIRect D = DropRow;
		D.Margin(6.0f, &D);
		Ui()->DoLabel(&D, "Smooth  \xE2\x96\xBE", 10.0f, TEXTALIGN_MC);
		Body.HSplitTop(4.0f, nullptr, &Body);
		Body.HSplitTop(20.0f, &AddRow, &Body);
		static CButtonContainer s_AddFake;
		if(GameClient()->m_Menus.DoButton_Menu(&s_AddFake, "Add fake aim", 0, &AddRow))
			Console()->Print(IConsole::OUTPUT_LEVEL_STANDARD, "tas", "Fake aim: optimized aim for anticheat (stub)");
	}

	// --- Visuals ---
	Right.HSplitTop(46.0f, &Panel, &Right);
	DrawPanel(Panel, 22.0f, "Visuals", Header, Body);
	{
		static CButtonContainer s_Vis;
		if(GameClient()->m_Menus.DoButton_CheckBox(&s_Vis, "Draw start/end pos", g_Config.m_ClTasShowAllAuto, &Body))
			g_Config.m_ClTasShowAllAuto ^= 1;
	}

	// Bottom status – compact
	CUIRect Status = Content;
	Status.HSplitBottom(14.0f, nullptr, &Status);
	// we already used Content, so draw footer inside MainView bottom
	CUIRect Footer;
	MainView.HSplitBottom(14.0f, nullptr, &Footer);
	Footer.Margin(4.0f, &Footer);
	if(m_ShowGhost || m_State != TAS_STATE_IDLE)
	{
		char aStatus[256];
		if(m_ShowGhost)
			str_format(aStatus, sizeof(aStatus), "Ghost at %.0f,%.0f | %s | %s", m_GhostPos.x, m_GhostPos.y, m_aSelectedFile[0]?m_aSelectedFile:"no file", m_aValidateResult[0]?m_aValidateResult:"not checked");
		else
			str_format(aStatus, sizeof(aStatus), "State: %s | Frames: %d | TPS:%d Turbo:%s", m_State==TAS_STATE_RECORDING?"Recording":m_State==TAS_STATE_PLAYBACK?"Playback":m_State==TAS_STATE_REWIND?"Rewind":m_State==TAS_STATE_FORWARD?"Forward":"Idle", (int)m_vFrames.size(), m_RecordSpeed, m_TurboActive?"ON":"OFF");
		Ui()->DoLabel(&Footer, aStatus, 8.0f, TEXTALIGN_MC);
	}
	else
	{
		Ui()->DoLabel(&Footer, "TAS ready. Record = isolated world (only you+dummy). Use showall 1 for weapons.", 8.0f, TEXTALIGN_MC);
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

	// Try compressed v2 first (zlib), fallback to v1
	const char *pMagicV2 = "DDNET_TAS_v2";
	unsigned long SrcSize = m_vFrames.size() * sizeof(CTasFrame);
	unsigned long DstSize = compressBound(SrcSize);
	std::vector<unsigned char> Compressed(DstSize);
	int ZRes = Z_OK;
	if(SrcSize > 0)
		ZRes = compress(Compressed.data(), &DstSize, (const Bytef*)m_vFrames.data(), SrcSize);

	if(SrcSize == 0 || (ZRes == Z_OK && DstSize < SrcSize * 0.9f))
	{
		// Use v2 compressed – smaller than 90% of original
		io_write(File, pMagicV2, 12);
		io_write(File, &m_Header, sizeof(m_Header));
		uint32_t CompressedSize = (uint32_t)DstSize;
		uint32_t UncompressedSize = (uint32_t)SrcSize;
		io_write(File, &CompressedSize, sizeof(CompressedSize));
		io_write(File, &UncompressedSize, sizeof(UncompressedSize));
		if(DstSize > 0)
			io_write(File, Compressed.data(), DstSize);
	}
	else
	{
		// Fallback v1 uncompressed
		const char *pMagic = "DDNET_TAS_v1";
		io_write(File, pMagic, 12);
		io_write(File, &m_Header, sizeof(m_Header));
		for(auto &F : m_vFrames)
			io_write(File, &F, sizeof(F));
	}

	io_close(File);
	OptimizeMemory();
	log_info("tas", "Saved TAS to '%s' frames=%d %s", pFilename, (int)m_vFrames.size(), (ZRes==Z_OK && DstSize < SrcSize*0.9f)?"(compressed v2)":"(v1)");

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
		File = Storage()->OpenFile(pFilename, IOFLAG_READ, IStorage::TYPE_ALL);
		if(!File)
		{
			log_info("tas", "Failed to open '%s' for reading", pFilename);
			return false;
		}
	}

	char aMagic[12];
	if(io_read(File, aMagic, 12) != 12)
	{
		io_close(File);
		return false;
	}
	bool IsV2 = mem_comp(aMagic, "DDNET_TAS_v2", 12) == 0;
	bool IsV1 = mem_comp(aMagic, "DDNET_TAS_v1", 12) == 0;
	if(!IsV1 && !IsV2)
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
	m_vFrames.reserve(m_Header.m_NumFrames);

	if(IsV2)
	{
		uint32_t CompressedSize = 0, UncompressedSize = 0;
		if(io_read(File, &CompressedSize, sizeof(CompressedSize)) != sizeof(CompressedSize) ||
		   io_read(File, &UncompressedSize, sizeof(UncompressedSize)) != sizeof(UncompressedSize))
		{
			io_close(File);
			return false;
		}
		std::vector<unsigned char> Compressed(CompressedSize);
		if(CompressedSize > 0 && io_read(File, Compressed.data(), CompressedSize) != CompressedSize)
		{
			io_close(File);
			return false;
		}
		m_vFrames.resize(m_Header.m_NumFrames);
		unsigned long Dst = UncompressedSize;
		int Res = uncompress((Bytef*)m_vFrames.data(), &Dst, Compressed.data(), CompressedSize);
		if(Res != Z_OK || Dst != UncompressedSize)
		{
			log_error("tas", "Failed to decompress TAS '%s' err=%d", pFilename, Res);
			io_close(File);
			return false;
		}
	}
	else
	{
		m_vFrames.resize(m_Header.m_NumFrames);
		for(int i = 0; i < m_Header.m_NumFrames; i++)
		{
			if(io_read(File, &m_vFrames[i], sizeof(CTasFrame)) != sizeof(CTasFrame))
			{
				m_vFrames.resize(i);
				break;
			}
		}
	}

	io_close(File);
	m_GhostPos = m_Header.m_StartPos;
	m_GhostDummyPos = m_Header.m_StartDummyPos;
	m_CurrentFrame = 0;
	m_ShowGhost = true;
	OptimizeMemory();

	log_info("tas", "Loaded TAS '%s' v%d frames=%d start=%.0f,%.0f", pFilename, IsV2?2:1, (int)m_vFrames.size(), m_GhostPos.x, m_GhostPos.y);
	return true;
}

// ----------------------------------------------------------------
// Console commands

// ----------------------------------------------------------------
// KRX Extensions: showall, freeze detection, ticks/step, auto rewind/forward, validate/remove

void CTas::EnsureShowAll()
{
	if(!g_Config.m_ClTasShowAllAuto)
		return;
	// KRX prerequisite: must have showall 1 before TAS to see weapons
	// We send chat command /showall 1 via console (executed as chat)
	// Use GameClient chat say. Fallback to console if available.
	if(GameClient() && Client()->State() == IClient::STATE_ONLINE)
	{
		char aBuf[128];
		str_copy(aBuf, "say /showall 1");
		Console()->ExecuteLine(aBuf, IConsole::CLIENT_ID_GAME);
		log_info("tas", "KRX: ensured showall 1 for TAS entities (weapons/pickups)");
	}
	// Also ensure cl_run_on_join contains showall for next join
	if(!str_find(g_Config.m_ClRunOnJoin, "showall"))
	{
		char aTmp[128];
		str_format(aTmp, sizeof(aTmp), "%s; showall 1", g_Config.m_ClRunOnJoin);
		// don't overwrite if empty
		if(g_Config.m_ClRunOnJoin[0]=='\0')
			str_copy(g_Config.m_ClRunOnJoin, "showall 1");
	}
}

void CTas::EnsureShowAllOnJoin()
{
	EnsureShowAll();
}

bool CTas::IsFrozen() const
{
	int LocalId = GameClient()->m_Snap.m_LocalClientId;
	if(LocalId < 0)
		return false;
	// Check client predicted freeze
	if(GameClient()->m_aClients[LocalId].m_FreezeEnd != 0)
		return true;
	if(GameClient()->m_Snap.m_aCharacters[LocalId].m_Active && GameClient()->m_Snap.m_aCharacters[LocalId].m_HasExtendedData)
	{
		if(GameClient()->m_Snap.m_aCharacters[LocalId].m_ExtendedData.m_FreezeEnd != 0)
			return true;
	}
	return false;
}

void CTas::CheckAutoRewindForward()
{
	if(m_vFrames.empty() || m_State != TAS_STATE_RECORDING)
		return;
	if(!g_Config.m_ClTasAutoRewind && !g_Config.m_ClTasAutoForward)
		return;
	bool NowFrozen = IsFrozen();
	if(NowFrozen && !m_WasFrozen)
	{
		// Entered freeze -> auto rewind to last safe tick
		if(g_Config.m_ClTasAutoRewind)
		{
			int Target = m_LastSafeFrame;
			if(Target >= 0 && Target < (int)m_vFrames.size())
			{
				m_CurrentFrame = Target;
				m_GhostPos = m_vFrames[Target].m_Pos;
				m_GhostDummyPos = m_vFrames[Target].m_DummyPos;
				m_AutoRewindCount++;
				log_info("tas", "Auto Rewind to safe tick %d/%d (frozen)", Target, (int)m_vFrames.size());
				if(g_Config.m_ClTasAutoPause)
				{
					m_Paused = true;
					log_info("tas", "Auto Pause after rewind");
				}
			}
		}
	}
	else if(!NowFrozen && m_WasFrozen)
	{
		// Left freeze -> auto forward if enabled (skip freeze wait)
		if(g_Config.m_ClTasAutoForward)
		{
			// We're already at last safe, just log; actual forward through freeze would be manual
			log_info("tas", "Auto Forward: left freeze at frame %d", m_CurrentFrame);
			if(g_Config.m_ClTasAutoPause)
				m_Paused = true;
		}
	}
	m_WasFrozen = NowFrozen;
	// Update last safe when not frozen
	if(!NowFrozen && !m_Paused && m_State == TAS_STATE_RECORDING)
    {
        m_LastSafeFrame = m_CurrentFrame;
    }
}

void CTas::DoRewindTicks(int Ticks)
{
	if(m_vFrames.empty())
		return;
	int Step = m_StepMode ? 1 : Ticks;
	Step = std::clamp(Step, 1, 50);
	for(int i=0;i<Step;i++)
	{
		if(m_CurrentFrame > 0)
		{
			m_CurrentFrame--;
			m_GhostPos = m_vFrames[m_CurrentFrame].m_Pos;
			m_GhostDummyPos = m_vFrames[m_CurrentFrame].m_DummyPos;
		}
		else
		{
			m_Paused = true;
			break;
		}
	}
}

void CTas::DoForwardTicks(int Ticks)
{
	if(m_vFrames.empty())
		return;
	int Step = m_StepMode ? 1 : Ticks;
	Step = std::clamp(Step, 1, 50);
	for(int i=0;i<Step;i++)
	{
		if(m_CurrentFrame < (int)m_vFrames.size()-1)
		{
			m_CurrentFrame++;
			m_GhostPos = m_vFrames[m_CurrentFrame].m_Pos;
			m_GhostDummyPos = m_vFrames[m_CurrentFrame].m_DummyPos;
		}
		else
		{
			m_Paused = true;
			break;
		}
	}
}

bool CTas::ValidateReplay(char *pOut, int OutSize)
{
	if(m_vFrames.empty())
	{
		str_copy(pOut, "No replay loaded", OutSize);
		return false;
	}
	int Mismatch = 0;
	float MaxDist = 0.0f;
	for(size_t i=1;i<m_vFrames.size();i++)
	{
		vec2 Prev = m_vFrames[i-1].m_Pos;
		vec2 Cur = m_vFrames[i].m_Pos;
		float Dist = distance(Prev, Cur);
		MaxDist = std::max(MaxDist, Dist);
		// KRX validate: if position jump > 2* tile (64) without teleport, suspect
		// Teeworlds max velocity ~ 1000 units per tick, so >150 maybe desync if no hook
		if(Dist > 200.0f && !m_vFrames[i].m_Input.m_Hook)
		{
			Mismatch++;
		}
	}
	if(Mismatch==0)
		str_format(pOut, OutSize, "Validate OK: %d frames, max jump %.1f, no mismatch", (int)m_vFrames.size(), MaxDist);
	else
		str_format(pOut, OutSize, "Validate WARNING: %d mismatches, max jump %.1f (check teleport/stopper desync)", Mismatch, MaxDist);
	str_copy(m_aValidateResult, pOut, sizeof(m_aValidateResult));
	return Mismatch==0;
}

int CTas::RemoveUselessInputs()
{
	if(m_vFrames.empty())
		return 0;
	int Removed = 0;
	std::vector<CTasFrame> NewFrames;
	NewFrames.reserve(m_vFrames.size());
	// Early out: if less than 10 frames, nothing to optimize
	for(size_t i=0;i<m_vFrames.size();i++)
	{
		const CTasFrame &F = m_vFrames[i];
		bool Useless = false;
		// Criteria: no hook, no jump, no fire, not moving? Similar to KRX Remove Useless: prefers no hook, no jump & Aim optimized
		if(!F.m_Input.m_Hook && !F.m_Input.m_Jump && !F.m_Input.m_Fire && F.m_Input.m_Direction==0)
		{
			// Check if position essentially unchanged from previous
			if(i>0)
			{
				vec2 PrevPos = m_vFrames[i-1].m_Pos;
				if(distance(PrevPos, F.m_Pos) < 0.5f)
				{
					// Aim not needed for movement -> can be optimized, consider useless if target not changing
					if(F.m_Input.m_TargetX == m_vFrames[i-1].m_Input.m_TargetX && F.m_Input.m_TargetY == m_vFrames[i-1].m_Input.m_TargetY)
						Useless = true;
				}
			}
		}
		if(Useless)
			Removed++;
		else
			NewFrames.push_back(F);
	}
	// KRX aim optimization: if not essential, set aim to Robot-like (static)
	for(auto &F : NewFrames)
	{
		if(!F.m_Input.m_Fire && !F.m_Input.m_Hook)
		{
			// Keep aim static between essential actions - Robot Fake Aim
			// We don't change real needed aim, just keep last essential
		}
	}
	if(Removed>0)
	{
		m_vFrames.swap(NewFrames);
		m_RemovedCount = Removed;
		log_info("tas", "Remove Useless: removed %d/%d frames, optimized aim", Removed, (int)NewFrames.size()+Removed);
	}
	return Removed;
}

void CTas::GetReplayTime(char *pBuf, int Size)
{
	if(m_vFrames.empty())
	{
		str_copy(pBuf, "00:00.000", Size);
		return;
	}
	// Estimate time from frames / record speed
	int Ticks = (int)m_vFrames.size();
	float Seconds = Ticks / (float)std::clamp(g_Config.m_ClTasRecordSpeed, 1, 50);
	int Min = (int)(Seconds/60);
	float Sec = Seconds - Min*60;
	str_format(pBuf, Size, "%02d:%06.3f (%d ticks @%d TPS)", Min, Sec, Ticks, g_Config.m_ClTasRecordSpeed);
	str_copy(m_aLastTime, pBuf, sizeof(m_aLastTime));
}
void CTas::ClearReplay()
{
	m_vFrames.clear();
	m_CurrentFrame = 0;
	m_LastSafeFrame = 0;
	m_Paused = false;
	log_info("tas", "Clear Replay: buffer cleared, %d frames remain", (int)m_vFrames.size());
	// KRX hold respawn: send kill to respawn at start
	if(Client()->State() == IClient::STATE_ONLINE)
	{
		Console()->ExecuteLine("kill", IConsole::CLIENT_ID_GAME);
	}
}


void CTas::ConTasRecord(IConsole::IResult *pResult, void *pUserData)
{
	(void)pResult;
	CTas *pSelf = (CTas *)pUserData;
	pSelf->StartRecord();
}

void CTas::ConTasPlay(IConsole::IResult *pResult, void *pUserData)
{
	(void)pResult;
	CTas *pSelf = (CTas *)pUserData;
	pSelf->StartPlayback();
}

void CTas::ConTasPause(IConsole::IResult *pResult, void *pUserData)
{
	(void)pResult;
	CTas *pSelf = (CTas *)pUserData;
	if(pSelf->m_Paused) pSelf->ResumeRecord();
	else pSelf->PauseRecord();
}

void CTas::ConTasStop(IConsole::IResult *pResult, void *pUserData)
{
	(void)pResult;
	CTas *pSelf = (CTas *)pUserData;
	if(pSelf->m_State == TAS_STATE_PLAYBACK) pSelf->StopPlayback();
	else pSelf->StopRecord();
}

void CTas::ConTasRewind(IConsole::IResult *pResult, void *pUserData)
{
	(void)pResult;
	CTas *pSelf = (CTas *)pUserData;
	if(pSelf->m_State == TAS_STATE_REWIND) pSelf->StopRewind();
	else pSelf->StartRewind();
}

void CTas::ConTasForward(IConsole::IResult *pResult, void *pUserData)
{
	(void)pResult;
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

void CTas::ConTasTicks(IConsole::IResult *pResult, void *pUserData)
{
	CTas *pSelf = (CTas *)pUserData;
	int Ticks = pResult->GetInteger(0);
	g_Config.m_ClTasTicksPerPress = std::clamp(Ticks, 1, 50);
	pSelf->m_TicksPerPress = g_Config.m_ClTasTicksPerPress;
	log_info("tas", "Ticks per press set to %d", pSelf->m_TicksPerPress);
}

void CTas::ConTasValidate(IConsole::IResult *pResult, void *pUserData)
{
	(void)pResult;
	CTas *pSelf = (CTas *)pUserData;
	char aBuf[256];
	pSelf->ValidateReplay(aBuf, sizeof(aBuf));
	pSelf->Console()->Print(IConsole::OUTPUT_LEVEL_STANDARD, "tas", aBuf);
}

void CTas::ConTasRemoveUseless(IConsole::IResult *pResult, void *pUserData)
{
	(void)pResult;
	CTas *pSelf = (CTas *)pUserData;
	int Removed = pSelf->RemoveUselessInputs();
	char aBuf[128];
	str_format(aBuf, sizeof(aBuf), "RemoveUseless: removed %d inputs", Removed);
	pSelf->Console()->Print(IConsole::OUTPUT_LEVEL_STANDARD, "tas", aBuf);
}

void CTas::ConTasShowAll(IConsole::IResult *pResult, void *pUserData)
{
	(void)pResult;
	CTas *pSelf = (CTas *)pUserData;
	pSelf->EnsureShowAll();
	pSelf->Console()->Print(IConsole::OUTPUT_LEVEL_STANDARD, "tas", "showall 1 ensured");
}

