package app.poolhouse.phone.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.poolhouse.core.theme.Brand
import app.poolhouse.phone.Tab
import app.poolhouse.phone.ui.theme.Icons
import app.poolhouse.theme.Navy
import app.poolhouse.theme.RailText
import app.poolhouse.theme.tint

private fun Tab.icon(): ImageVector = when (this) {
    Tab.CHATS -> Icons.Chat
    Tab.POOL -> Icons.Pool
    Tab.WORK -> Icons.Work
    Tab.INBOX -> Icons.Bell
}

/** The web's navy rail, laid along the bottom where a thumb reaches it: each place in its own colour, the open one filled with it. */
@Composable
fun BottomBar(selected: Tab, waiting: Int, onSelect: (Tab) -> Unit) {
    Row(
        Modifier.fillMaxWidth().background(Navy).navigationBarsPadding().padding(horizontal = 8.dp, vertical = 6.dp).selectableGroup(),
        horizontalArrangement = Arrangement.SpaceAround,
    ) {
        for (tab in Tab.entries) {
            val on = tab == selected
            val tint = tab.section().tint()
            val ink = if (on) Color(Brand.NAVY) else tint
            Box(Modifier.weight(1f), contentAlignment = Alignment.TopCenter) {
                Column(
                    Modifier.width(76.dp).height(54.dp).clip(RoundedCornerShape(13.dp))
                        .background(if (on) tint else Color.Transparent)
                        .selectable(on, role = Role.Tab) { onSelect(tab) }
                        .padding(vertical = 6.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Icon(tab.icon(), contentDescription = null, tint = ink, modifier = Modifier.size(24.dp))
                    Text(tab.label, style = MaterialTheme.typography.labelMedium, fontSize = 11.sp, color = if (on) ink else RailText)
                }
                if (tab == Tab.INBOX && waiting > 0) {
                    Text(
                        "$waiting",
                        style = MaterialTheme.typography.labelSmall,
                        color = Color(Brand.NAVY),
                        modifier = Modifier.offset(x = 18.dp).clip(RoundedCornerShape(8.dp)).background(Color(Brand.YELLOW)).padding(horizontal = 5.dp),
                    )
                }
            }
        }
    }
}
