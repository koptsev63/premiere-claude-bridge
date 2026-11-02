# Какие данные отправляет мост и зачем

Коротко: мост по умолчанию отправляет обезличенную схему вашего монтажа, чтобы ассистент учился резать так, как режут живые монтажёры. Видео, звук, имена файлов и названия проектов не уходят никогда. Не хотите: снимите одну галочку, и всё отправленное удалится.

## Как это включается и выключается

1. Отправка включена по умолчанию с первого запуска версии 0.8.3. Уходит только работа после этого момента. Всё, что уже лежит на компьютере (старые проекты, их автосохранения, журнал), остаётся здесь.
2. При первой проверке связи Claude один раз говорит вам, что отправка включена и как её выключить. В панели Claude Bridge в Premiere галочка «Помогать обучать ассистента» стоит, рядом написано «Включено по умолчанию».
3. Не хотите: снимите галочку или скажите Claude «не отправляй журнал». Отправка останавливается, всё уже отправленное удаляется с сервера сразу. Это решение сохраняется и при обновлениях моста.
4. Если вы выключили отправку в версии 0.8.1 или 0.8.2, она останется выключенной.
5. Захотите снова: поставьте галочку. Пойдёт только работа с этого момента.

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
- Основание: законный интерес автора моста в том, чтобы ассистент монтажа становился лучше (статья 6.1.f GDPR). Данные обезличены, и вы вправе возразить в любой момент (статья 21): одна галочка или одна фраза, отправка останавливается, отправленное удаляется.
- Что уже успело войти в обученные правила ассистента, отозвать нельзя. Сами записи удаляются.

## Чужой материал и NDA

Любой проект можно исключить: в панели поставьте галочку «Не отправлять этот проект» или скажите Claude «не отправляй этот проект». Из него не уйдёт ничего, ни сейчас, ни потом, даже если вы позже вернёте его обратно: снова пойдёт только новая работа. Что ушло до исключения, остаётся на сервере, пока вы не снимете главную галочку (тогда удаляется всё).

Если вы монтируете проект клиента под NDA, исключите его до начала работы или выключите отправку, пока не согласуете с заказчиком. Мост не отправляет ни картинку, ни имена, но решение за вами и за владельцем проекта.

Эта страница и есть политика конфиденциальности моста.

---

# What the bridge sends, and why (English)

On by default from 0.8.3, off with one click. The bridge sends an anonymized outline of your edit: cut points, frames trimmed from heads and tails, moves, splits, speed changes, J/L cuts, shot lengths, cuts per minute, frame size and rate, timestamps and file extensions. It never sends video, audio, captions, or any file, clip, sequence, project or folder name, marker text, your name, email or IP address.

Only work done after the first run of 0.8.3 (or after you turn it back on) is sent; older projects, autosaves and journal entries stay on your machine. Any project can be kept out for good (panel checkbox «Не отправлять этот проект», or tell Claude «don't send this project»); work done while it is out never goes. Names become salted hashes; the salt never leaves your computer. You appear as a random install id. Claude tells you once that it is on and how to turn it off. Turning it off (panel checkbox, or tell Claude «don't send my journal») stops it for good, survives updates and deletes everything sent under that id at once and drops the id and salt. Preview the next send with `node mcp-server/pk.js share preview`.

Controller: Vladimir Koptsev, Barcelona, koptsev63@gmail.com. Server in the EU (Hetzner, Helsinki). Kept up to 3 years. Not sold or shared for third-party training. Lawful basis: legitimate interest in improving the editing assistant (GDPR art. 6(1)(f)), with anonymized data and the right to object at any time (art. 21) by one click. A no given in 0.8.1 or 0.8.2 stays a no. What already shaped trained rules cannot be recalled; the records themselves are deleted. This page is the bridge's privacy policy.
