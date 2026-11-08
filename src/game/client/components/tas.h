#ifndef GAME_CLIENT_COMPONENTS_TAS_H
#define GAME_CLIENT_COMPONENTS_TAS_H

#include <game/client/component.h>
#include <base/io.h>
#include <base/types.h>
#include <base/vmath.h>
#include <generated/protocol.h>

#include <vector>
#include <string>

struct CTasFrame
{
	int m_Tick;
	CNetObj_PlayerInput m_Input;
	CNetObj_PlayerInput m_DummyInput;
	vec2 m_Pos;
	vec2 m_DummyPos;
	int m_Weapon;
	int m_DummyWeapon;
	bool m_HasWeapon[NUM_WEAPONS];
	bool m_DummyHasWeapon[NUM_WEAPONS];
};

struct CTasHeader
{
	char m_aMapName[64];
	char m_aAuthor[32];
	int m_RecordTickSpeed;
	int m_StartTick;
	vec2 m_StartPos;
	vec2 m_StartDummyPos;
	int m_NumFrames;
};

class CTas : public CComponent
{
public:
	enum ETasState
	{
		TAS_STATE_IDLE = 0,
		TAS_STATE_RECORDING,
		TAS_STATE_PAUSED,
		TAS_STATE_PLAYBACK,
		TAS_STATE_REWIND,
		TAS_STATE_FORWARD,
	};

	enum ETasWorld
	{
		TAS_WORLD_REAL = 0,
		TAS_WORLD_ISOLATED,
	};

private:
	ETasState m_State = TAS_STATE_IDLE;
	ETasWorld m_World = TAS_WORLD_REAL;
	bool m_IsolatedWorld = false;
	bool m_Paused = false;
	bool m_TurboActive = false;

	// Config-driven speeds 1-50
	int m_RecordSpeed = 20; // ticks per second
	int m_RewindSpeed = 20;
	int m_ForwardSpeed = 20;

	// Playback
	int m_CurrentFrame = 0;
	int m_PlaybackTick = 0;
	float m_RecordAccum = 0.0f;
	float m_PlaybackAccum = 0.0f;
	float m_RewindAccum = 0.0f;
	float m_ForwardAccum = 0.0f;

	// Data
	std::vector<CTasFrame> m_vFrames;
	CTasHeader m_Header;

	// Ghost preview
	bool m_ShowGhost = false;
	vec2 m_GhostPos;
	vec2 m_GhostDummyPos;
	int m_GhostWeapon;
	char m_aSelectedFile[IO_MAX_PATH_LENGTH];
	char m_aSaveName[128];

	// File handling
	std::vector<std::string> m_vTasFiles;
	int m_SelectedIndex = -1;

	// Internal helpers
	void UpdateSpeedsFromConfig();
	void EnsureTasFolder();
	void RefreshFileList();
	void GiveAllWeapons();
	bool ShouldHidePlayer(int ClientId);

	// Tick logic
	void DoRecordTick();
	void DoRewindTick();
	void DoForwardTick();
	void DoPlaybackTick();

	static void ConTasRecord(IConsole::IResult *pResult, void *pUserData);
	static void ConTasPlay(IConsole::IResult *pResult, void *pUserData);
	static void ConTasPause(IConsole::IResult *pResult, void *pUserData);
	static void ConTasStop(IConsole::IResult *pResult, void *pUserData);
	static void ConTasRewind(IConsole::IResult *pResult, void *pUserData);
	static void ConTasForward(IConsole::IResult *pResult, void *pUserData);
	static void ConTasSave(IConsole::IResult *pResult, void *pUserData);
	static void ConTasLoad(IConsole::IResult *pResult, void *pUserData);
	static void ConTasIsolated(IConsole::IResult *pResult, void *pUserData);
	static void ConTasTurbo(IConsole::IResult *pResult, void *pUserData);

public:
	int Sizeof() const override { return sizeof(*this); }

	void OnInit() override;
	void OnConsoleInit() override;
	void OnReset() override;
	void OnUpdate() override;
	void OnRender() override;
	bool OnInput(const IInput::CEvent &Event) override;
	void OnStateChange(int NewState, int OldState) override;
	void OnNewSnapshot() override;

	// API for other components
	bool IsIsolatedWorld() const { return m_IsolatedWorld; }
	bool IsRecording() const { return m_State == TAS_STATE_RECORDING; }
	bool IsPaused() const { return m_Paused; }
	bool IsPlaying() const { return m_State == TAS_STATE_PLAYBACK; }
	bool IsRewinding() const { return m_State == TAS_STATE_REWIND; }
	bool IsForwarding() const { return m_State == TAS_STATE_FORWARD; }
	ETasState GetState() const { return m_State; }
	bool ShouldHideOtherPlayers(int ClientId) const;

	// Controls
	void StartRecord();
	void StopRecord();
	void PauseRecord();
	void ResumeRecord();
	void StartPlayback();
	void StopPlayback();
	void StartRewind();
	void StopRewind();
	void StartForward();
	void StopForward();
	void SetTurbo(bool Active) { m_TurboActive = Active; }

	// File I/O
	bool SaveToFile(const char *pFilename);
	bool LoadFromFile(const char *pFilename);
	void RefreshTasList() { RefreshFileList(); }
	const std::vector<std::string> &GetFileList() const { return m_vTasFiles; }
	int GetSelectedIndex() const { return m_SelectedIndex; }
	void SetSelectedIndex(int Index);
	const char *GetSelectedFile() const { return m_aSelectedFile; }
	vec2 GetGhostPos() const { return m_GhostPos; }
	bool IsGhostVisible() const { return m_ShowGhost; }

	// UI helpers
	void RenderTasHud();
	void RenderGhostPreview();

	// For menus
	void DoTasMenu(CUIRect MainView);
};

#endif
