import RallyKit
import SwiftUI

/// The address field and its connection test, shared by first run and Settings.
struct ServerForm: View {
    @Environment(AppModel.self) private var model
    @State private var address: String
    @State private var testing = false
    @State private var message: String?
    @State private var succeeded = false
    let buttonTitle: String
    let onConnected: () -> Void

    init(initial: String = "", buttonTitle: String = "Connect", onConnected: @escaping () -> Void) {
        _address = State(initialValue: initial)
        self.buttonTitle = buttonTitle
        self.onConnected = onConnected
    }

    var body: some View {
        VStack(alignment: .leading, spacing: RallyDesign.space[3]) {
            TextField("rally.your-tailnet.ts.net:8000", text: $address)
                .textContentType(.URL)
                .keyboardType(.URL)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.go)
                .onSubmit { Task { await connect() } }
                .padding(RallyDesign.space[3])
                .frame(minHeight: RallyDesign.targetMin)
                .background(RallyDesign.color("surfaceSunken"), in: RoundedRectangle(cornerRadius: 8))
                .accessibilityIdentifier("server-address")

            Button {
                Task { await connect() }
            } label: {
                HStack {
                    if testing { ProgressView() }
                    Text(testing ? "Connecting…" : buttonTitle)
                }
                .frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin)
            }
            .buttonStyle(.borderedProminent)
            .disabled(testing || address.trimmingCharacters(in: .whitespaces).isEmpty)
            .accessibilityIdentifier("server-connect")

            if let message {
                Label(message, systemImage: succeeded ? "checkmark.circle" : "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(succeeded ? RallyDesign.color("ink") : RallyDesign.color("stateOverdue"))
                    .accessibilityIdentifier("server-message")
            }
        }
    }

    private func connect() async {
        testing = true
        message = nil
        defer { testing = false }
        switch await model.connect(to: address) {
        case .connected(let count):
            succeeded = true
            message = "Connected — \(count) family member\(count == 1 ? "" : "s")."
            onConnected()
        case .failed(let text):
            succeeded = false
            message = text
        }
    }
}
