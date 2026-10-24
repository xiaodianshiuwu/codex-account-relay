import AppKit
import CodexRelayCore
import SwiftUI

struct AccountAvatar: View {
    @EnvironmentObject private var model: AppModel
    let account: RelayAccount
    var size: CGFloat = 36
    var cornerRadius: CGFloat = 5

    private var initial: String {
        String(account.preferredName.prefix(1)).uppercased()
    }

    var body: some View {
        Group {
            if let image = model.avatarImage(for: account) {
                Image(nsImage: image)
                    .resizable()
                    .scaledToFill()
            } else {
                ZStack {
                    LinearGradient(
                        colors: [RelayTheme.accent.opacity(0.92), RelayTheme.purple.opacity(0.92)],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                    Text(initial)
                        .font(.hudDisplay(size * 0.46))
                        .foregroundStyle(RelayTheme.onAccent)
                }
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                .strokeBorder(RelayTheme.accent.opacity(0.30), lineWidth: 0.8)
        }
    }
}
