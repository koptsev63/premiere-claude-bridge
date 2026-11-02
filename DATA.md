# Какие данные отправляет мост и зачем

Коротко: ничего, пока вы сами не скажете «да». Если скажете, мост отправляет обезличенную схему вашего монтажа, чтобы ассистент учился резать так, как режут живые монтажёры. Видео, звук, имена файлов и названия проектов не уходят никогда.

## Как вас спрашивают

1. При первой проверке связи Claude один раз задаёт вопрос: «Помочь обучить ассистента монтажу?» Ответы: «Да, помогать» и «Не сейчас». Молчание = нет.
2. В панели Claude Bridge в Premiere есть галочка «Помогать обучать ассистента». По умолчанию она снята.
3. Передумали: снимите галочку или скажите Claude «не отправляй мои данные». Всё, что уже было отправлено, удаляется с сервера сразу.

## Что уходит

- Решения монтажа: где разрез, сколько кадров снято с начала и с конца плана, куда сдвинут клип, где склейка разрезана, где изменена скорость, J- и L-склейки.
- Форма секвенции: длины планов, сколько склеек в минуту, размер кадра, частота кадров.
- Время решения (дата и час) и расширение файла (mov, mp4), без имени.

## Что не уходит

- Кадры, видео, звук, субтитры.
- Имена файлов, клипов, секвенций, проектов, папок, тексты маркеров и комментариев.
- Ваше имя, почта, IP-адрес. Сервер IP не записывает.

## Как обезличивается

Каждое имя заменяется на код вида `h3f9a1c0b2d4e`. Код считается с секретом, который лежит только на вашем компьютере, поэтому на сервере его нельзя превратить обратно в имя, даже угадывая. Вместо вас на сервере значится случайный номер установки. Если вы выключите отправку, номер и секрет стираются, а при новом включении создаются новые.

Посмотреть, что уйдёт при следующей отправке, можно в любой момент: скажите Claude «покажи, что мост отправит» или выполните `node mcp-server/pk.js share preview`.

## Кто и сколько хранит

- Получатель: Владимир Копцев, режиссёр, Барселона, автор моста. Связь: koptsev63@gmail.com, чат монтажёров https://t.me/+Fw5nSiqUkCswNjBi
- Сервер в Европейском союзе (Hetzner, Хельсинки).
- Срок хранения: до 3 лет, потом удаляется. Раньше, если вы выключили отправку или попросили удалить.
- Данные не продаются и не передаются третьим лицам для обучения их моделей.
- Основание: ваше согласие (статья 6.1.a GDPR). Его можно отозвать так же легко, как дать.
- Что уже успело войти в обученные правила ассистента, отозвать нельзя. Сами записи удаляются.

## Чужой материал

Если вы монтируете проект клиента под NDA, не включайте отправку, пока не согласуете с заказчиком. Мост не отправляет ни картинку, ни имена, но решение за вами и за владельцем проекта.

---

# What the bridge sends, and why (English)

Nothing until you say yes. With your yes, the bridge sends an anonymized outline of your edit: cut points, frames trimmed from heads and tails, moves, splits, speed changes, J/L cuts, shot lengths, cuts per minute, frame size and rate, timestamps and file extensions. It never sends video, audio, captions, or any file, clip, sequence, project or folder name, marker text, your name, email or IP address.

Names become salted hashes; the salt never leaves your computer. You appear as a random install id. Saying no later (panel checkbox, or tell Claude) deletes everything sent under that id at once and drops the id and salt. Preview the next send with `node mcp-server/pk.js share preview`.

Controller: Vladimir Koptsev, Barcelona, koptsev63@gmail.com. Server in the EU (Hetzner, Helsinki). Kept up to 3 years. Not sold or shared for third-party training. Lawful basis: consent (GDPR art. 6(1)(a)), withdrawable as easily as given. What already shaped trained rules cannot be recalled; the records themselves are deleted.
