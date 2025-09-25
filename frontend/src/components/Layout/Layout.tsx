import { Box } from '@mui/material'
import Header from '@/components/Layout/Header'
import { ReactNode } from 'react';


type Props = {
    pageTitle: string;
    children: ReactNode

}

export default function Layout({ pageTitle, children }: Props) {

    return (

        <Box width={'100%'}>
            <Header pageTitle={pageTitle} />

            <Box paddingInline={'1vw'}>
                {children}
            </Box>


        </Box>
    )
}