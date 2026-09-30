import RallyKit
import SwiftUI

/// A row of filter chips. There is no "All" chip anywhere in Rally: no
/// selection *is* the unfiltered state. `trailing` holds the one action that
/// belongs beside the chips it manages (Shopping's "Stores").
struct ChipBar<Trailing: View>: View {
    let chips: [FilterChip]
    let selected: Set<String>
    let toggle: (String) -> Void
    var clear: (() -> Void)?
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: RallyDesign.space[2]) {
                ForEach(chips) { chip in
                    let on = selected.contains(chip.value)
                    Button { toggle(chip.value) } label: {
                        Text(chip.title)
                            .padding(.horizontal, RallyDesign.space[3])
                            .frame(minHeight: RallyDesign.targetMin)
                            .background(on ? RallyDesign.color("ink") : RallyDesign.color("surfaceSunken"), in: Capsule())
                            .foregroundStyle(on ? RallyDesign.color("surface") : RallyDesign.color("ink"))
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
                if let clear, !selected.isEmpty {
                    Button("Clear", action: clear).frame(minHeight: RallyDesign.targetMin)
                }
                trailing()
            }
            .padding(.horizontal, RallyDesign.space[4])
        }
        .background(.bar)
    }
}

extension ChipBar where Trailing == EmptyView {
    init(chips: [FilterChip], selected: Set<String>, toggle: @escaping (String) -> Void, clear: (() -> Void)? = nil) {
        self.init(chips: chips, selected: selected, toggle: toggle, clear: clear) { EmptyView() }
    }
}

/// A member's identity color and name. The color is the one color on a page;
/// it comes from the closed palette, never a raw value a screen invents.
struct MemberLabel: View {
    let member: FamilyMember
    var body: some View {
        HStack(spacing: RallyDesign.space[1]) {
            Circle().fill(RallyDesign.memberColor(hex: member.color)).frame(width: 8, height: 8)
            Text(member.name)
        }
        .accessibilityElement(children: .combine)
    }
}
