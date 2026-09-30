import RallyKit
import SwiftUI

struct FamilyMembersView: View {
    @Environment(AppModel.self) private var app
    @State private var admin: FamilyAdminModel
    @State private var editing: MemberTarget?

    init(client: APIClient) { _admin = State(initialValue: FamilyAdminModel(client: client)) }

    var body: some View {
        List {
            ForEach(app.members) { member in
                Button { editing = .edit(member) } label: {
                    HStack {
                        MemberLabel(member: member)
                        Spacer()
                        if !(member.pushoverUserKey ?? "").isEmpty { Image(systemName: "bell").foregroundStyle(RallyDesign.color("inkMuted")).accessibilityLabel("Has a Pushover key") }
                    }
                    .frame(minHeight: RallyDesign.targetMin)
                }
                .buttonStyle(.plain)
                .accessibilityIdentifier("member-row-\(member.name)")
            }
        }
        .navigationTitle("Family Members")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button { editing = .add } label: { Image(systemName: "plus") }.accessibilityLabel("Add member") } }
        .task { await admin.loadKinds() }
        .sheet(item: $editing) { MemberEditor(admin: admin, target: $0) }
    }
}

enum MemberTarget: Identifiable {
    case add, edit(FamilyMember)
    var id: String { switch self { case .add: "add"; case .edit(let m): "edit-\(m.id)" } }
}

struct MemberEditor: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(AppModel.self) private var app
    let admin: FamilyAdminModel
    let target: MemberTarget
    @State private var draft = MemberDraft()
    @State private var pushResult: String?
    @State private var confirmDelete = false
    @State private var saving = false
    @State private var didSetUp = false

    private var original: FamilyMember? { if case .edit(let m) = target { m } else { nil } }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Name", text: $draft.name).accessibilityIdentifier("member-name")
                    // A closed palette of five: any two members stay distinguishable on any display,
                    // including monochrome e-ink. There is no free-form color.
                    HStack(spacing: RallyDesign.space[2]) {
                        ForEach(RallyDesign.memberPalette, id: \.hex) { entry in
                            let on = draft.color?.lowercased() == entry.hex
                            Button { draft.color = entry.hex } label: {
                                Circle().fill(RallyDesign.memberColor(hex: entry.hex)).frame(width: 30, height: 30)
                                    .overlay { if on { Image(systemName: "checkmark").foregroundStyle(.white).font(.caption.bold()) } }
                                    .frame(width: RallyDesign.targetMin, height: RallyDesign.targetMin)
                            }
                            .buttonStyle(.plain).accessibilityLabel(entry.label).accessibilityAddTraits(on ? .isSelected : [])
                        }
                    }
                } header: { Text("Identity") } footer: {
                    if original == nil { Text("Leave the color alone and Rally picks the next unused one.") }
                }
                Section {
                    TextField("Pushover user key", text: $draft.pushoverUserKey).textInputAutocapitalization(.never).autocorrectionDisabled()
                    TextField("Pushover device (optional)", text: $draft.pushoverDevice).textInputAutocapitalization(.never).autocorrectionDisabled()
                    if let original { Button("Send a test notification") { Task { pushResult = await admin.testPush(original) } } }
                    if let pushResult { Text(pushResult).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")) }
                } header: { Text("Pushover") } footer: {
                    Text("Without a key this person is never notified, which is the default rather than an error.")
                }
                Section {
                    ForEach(admin.notificationKinds) { kind in
                        Toggle(isOn: Binding(get: { draft.notifications[kind.kind] ?? kind.defaultOn },
                                             set: { draft.notifications[kind.kind] = $0 })) {
                            VStack(alignment: .leading, spacing: 2) { Text(kind.label); Text(kind.audience).font(.caption).foregroundStyle(RallyDesign.color("inkMuted")) }
                        }
                    }
                } header: { Text("Notify me about") } footer: {
                    if !admin.tokenConfigured { Text("No Pushover application token is set, so nothing sends yet. Add it under Household Settings.") }
                }
                if original != nil {
                    Section { Button("Delete Member", role: .destructive) { confirmDelete = true } }
                }
            }
            .navigationTitle(original == nil ? "Add Member" : "Edit Member")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }
                        .disabled(draft.name.trimmingCharacters(in: .whitespaces).isEmpty || saving).accessibilityIdentifier("member-save")
                }
            }
            .onAppear { if !didSetUp { didSetUp = true; if let original { draft = MemberDraft(original) } } }
            .confirmationDialog("Delete \(original?.name ?? "member")?", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("Delete", role: .destructive) { Task { if let o = original, await admin.delete(o) { await app.refresh(); dismiss() } } }
            } message: { Text("Their notification and device preferences are removed too.") }
            .alert("Couldn't do that", isPresented: Binding(get: { admin.errorMessage != nil }, set: { if !$0 { admin.errorMessage = nil } })) {
                Button("OK") {}
            } message: { Text(admin.errorMessage ?? "") }
        }
        .presentationDetents([.large])
    }

    private func save() async {
        saving = true
        defer { saving = false }
        if await admin.save(draft, editing: original) { await app.refresh(); dismiss() }
    }
}
