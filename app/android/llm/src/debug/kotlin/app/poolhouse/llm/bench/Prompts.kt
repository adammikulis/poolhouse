/** Invented material for measuring: a long text with a fact at each end, and a picture with a number in it. */
package app.poolhouse.llm.bench

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import java.io.ByteArrayOutputStream
import kotlin.random.Random

private val SHADES = listOf("amber", "cobalt", "crimson", "ivory", "jade", "ochre", "scarlet", "teal", "umber", "violet")
private val THINGS = listOf("anchor", "beacon", "compass", "dynamo", "falcon", "glacier", "harbor", "lantern", "meadow", "quarry")
private val GOODS = listOf("green tea", "slate tiles", "oak barrels", "linen bolts", "sea salt", "copper wire", "seed potatoes", "glass jars")
private val PLACES = listOf("northern pier", "customs shed", "rail siding", "cold store", "river landing", "weigh house", "east gate")
private val DAYS = listOf("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

/** A long text to be read, the two words hidden at its start and its end, and the question about them. */
data class Needle(val text: String, val first: String, val last: String)

fun needle(sentences: Int, seed: Int): Needle {
    val random = Random(seed)
    fun word() = "${SHADES.random(random)}-${THINGS.random(random)}-${random.nextInt(100, 1000)}"
    val first = word()
    val last = word()
    val text = StringBuilder()
    text.append("Read this ledger. At the end you are asked about it.\n")
    text.append("The access word of the NORTH archive is $first. Remember it.\n\n")
    for (entry in 1..sentences) {
        text.append(
            "Entry $entry: on ${DAYS.random(random)} the clerk counted ${random.nextInt(2, 90)} crates of " +
                "${GOODS.random(random)} arriving at the ${PLACES.random(random)} and moved them to the ${PLACES.random(random)}.\n",
        )
    }
    text.append("\nThe access word of the SOUTH archive is $last. Remember it.\n\n")
    text.append(
        "Two questions about the text above. 1. Its second line gave the access word of the NORTH archive: what is it? " +
            "2. Near its end it gave the access word of the SOUTH archive: what is it?\n" +
            "Reply with exactly one line in this form: NORTH=word, SOUTH=word",
    )
    return Needle(text.toString(), first, last)
}

/** A PNG showing `number` in large black digits beside a red circle. */
fun numberPicture(number: Int): ByteArray {
    val bitmap = Bitmap.createBitmap(768, 512, Bitmap.Config.ARGB_8888)
    val canvas = Canvas(bitmap)
    canvas.drawColor(Color.WHITE)
    val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    paint.color = Color.rgb(200, 20, 20)
    canvas.drawCircle(150f, 256f, 100f, paint)
    paint.color = Color.BLACK
    paint.textSize = 160f
    paint.isFakeBoldText = true
    canvas.drawText(number.toString(), 300f, 310f, paint)
    val out = ByteArrayOutputStream()
    bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)
    bitmap.recycle()
    return out.toByteArray()
}
