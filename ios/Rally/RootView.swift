import RallyKit
import SwiftUI

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        if model.hasServer {
            MainTabView()
                .id(model.serverURL) // a new server means every screen starts over
                .safeAreaInset(edge: .top, spacing: 0) {
                    if model.isUnreachable { UnreachableBanner() }
                }
        } else {
            OnboardingView()
        }
    }
}

/// Shown over any screen when the server stops answering. On a tailnet the
/// usual cause is the VPN being off, so the banner says that rather than
/// leaving a screen of spinners.
struct UnreachableBanner: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(spacing: RallyDesign.space[2]) {
            Image(systemName: "wifi.exclamationmark")
            VStack(alignment: .leading, spacing: 2) {
                Text("Can't reach Rally").font(.subheadline.weight(.semibold))
                Text("Check that Tailscale or your network is connected.").font(.caption)
            }
            Spacer()
            Button("Retry") { Task { await model.refresh() } }
                .buttonStyle(.bordered)
                .frame(minHeight: RallyDesign.targetMin)
        }
        .padding(.horizontal, RallyDesign.space[3])
        .padding(.vertical, RallyDesign.space[2])
        .foregroundStyle(RallyDesign.color("surface"))
        .background(RallyDesign.color("stateOverdue"))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("unreachable-banner")
    }
}
