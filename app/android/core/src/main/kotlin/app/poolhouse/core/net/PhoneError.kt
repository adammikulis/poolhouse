package app.poolhouse.core.net

/** Why a call did not give its result. [message] is one plain sentence for the person. */
sealed class PhoneError(message: String, cause: Throwable? = null) : Exception(message, cause) {
    /** No address of the computer answered, or the connection broke. */
    class Unreachable(message: String = "The computer cannot be reached.", cause: Throwable? = null) : PhoneError(message, cause)

    /** The computer no longer knows this phone: link it again. */
    class Unlinked(message: String = "This phone is no longer linked to the computer.") : PhoneError(message)

    /** The certificate at the address is not the linked computer's. */
    class WrongComputer : PhoneError("This is not the linked computer: its certificate is different.")

    /** The computer answered and said no. */
    class Refused(val status: Int, val code: String, message: String) : PhoneError(message)

    /** The answer was not the phone API. */
    class Unreadable(message: String = "The computer's answer could not be read.", cause: Throwable? = null) : PhoneError(message, cause)
}
