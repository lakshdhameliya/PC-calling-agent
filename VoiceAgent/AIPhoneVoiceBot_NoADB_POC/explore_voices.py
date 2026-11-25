import asyncio
import edge_tts

async def main():
    voices = await edge_tts.list_voices()
    indian = [v for v in voices if any(x in v.get('Locale', '').lower() for x in ['in', 'hi', 'gu'])]
    for v in indian:
        print(v.get('ShortName'), "|", v.get('Gender'), "|", v.get('Locale'), "|", v.get('FriendlyName'))

if __name__ == '__main__':
    asyncio.run(main())
