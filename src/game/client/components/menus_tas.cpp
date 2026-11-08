#include "menus.h"
#include "tas.h"

#include <game/client/gameclient.h>
#include <engine/storage.h>

void CMenus::RenderTas(CUIRect MainView)
{
	// Delegate to CTas component
	GameClient()->m_Tas.DoTasMenu(MainView);
}
