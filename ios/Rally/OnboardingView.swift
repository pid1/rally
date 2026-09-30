import RallyKit
import SwiftUI

/// First run: one question. Rally has no accounts, so the server's address is
/// the whole of signing in.
struct OnboardingView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: RallyDesign.space[5]) {
                VStack(alignment: .leading, spacing: RallyDesign.space[2]) {
                    Text("Rally")
                        .font(.system(size: 44, weight: .semibold, design: .serif))
                    Text("Your family's command center.")
                        .foregroundStyle(RallyDesign.color("inkMuted"))
                }

                VStack(alignment: .leading, spacing: RallyDesign.space[2]) {
                    Text("Where is your Rally server?").font(.headline)
                    Text("Enter the address you use in a browser. On Tailscale that's your server's MagicDNS name or 100.x address, with the port. Connect to Tailscale on this phone first.")
                        .font(.footnote)
                        .foregroundStyle(RallyDesign.color("inkMuted"))
                }

                ServerForm(onConnected: {})
            }
            .padding(RallyDesign.space[4])
            .frame(maxWidth: 560)
            .frame(maxWidth: .infinity)
            .padding(.top, RallyDesign.space[7])
        }
        .scrollDismissesKeyboard(.interactively)
        .background(RallyDesign.color("surface"))
    }
}
